import os
import threading
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify
from supabase import create_client, Client, ClientOptions

auth_bp = Blueprint('auth', __name__)

# Per-thread Supabase client — supabase>=2.0.0 uses httpx internally which is
# NOT safe to share across gunicorn gthread workers. threading.local() gives
# each thread its own httpx connection context, eliminating deadlocks on
# concurrent requests (e.g. /video/profile + /video/history fetched in parallel).
# Timeout of 10s prevents hanging threads that kill the gunicorn worker (502).
_sb_local: threading.local = threading.local()


def _get_sb() -> Client:
    if not getattr(_sb_local, 'client', None):
        _sb_local.client = create_client(
            os.getenv('SUPABASE_URL', ''),
            os.getenv('SUPABASE_ANON_KEY', ''),
            options=ClientOptions(
                postgrest_client_timeout=10,
                storage_client_timeout=10,
            ),
        )
    return _sb_local.client


def _try_refresh() -> bool:
    """Attempt to refresh the Supabase session using the stored refresh_token.
    Returns True and updates session on success, False otherwise."""
    refresh_token = session.get('refresh_token')
    if not refresh_token:
        return False
    try:
        sb     = _get_sb()
        result = sb.auth.refresh_session(refresh_token)
        session['access_token']  = result.session.access_token
        session['refresh_token'] = result.session.refresh_token
        return True
    except Exception:
        return False


def require_auth(f):
    """Decorator: redirect to login if no valid session.
    Auto-refreshes expired tokens before giving up."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = session.get('access_token')
        if not token:
            return redirect(url_for('auth.login'))
        try:
            user = _get_sb().auth.get_user(token)
            request.current_user = user.user
        except Exception:
            # Token expired — try silent refresh
            if _try_refresh():
                try:
                    user = _get_sb().auth.get_user(session['access_token'])
                    request.current_user = user.user
                except Exception:
                    session.clear()
                    return redirect(url_for('auth.login'))
            else:
                session.clear()
                return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def require_auth_api(f):
    """API variant: returns 401 JSON instead of redirect.
    Auto-refreshes expired tokens before giving up."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = session.get('access_token')
        if not token:
            return jsonify({'error': 'Unauthorized'}), 401
        try:
            user = _get_sb().auth.get_user(token)
            request.current_user = user.user
        except Exception:
            # Token expired — try silent refresh
            if _try_refresh():
                try:
                    user = _get_sb().auth.get_user(session['access_token'])
                    request.current_user = user.user
                except Exception:
                    session.clear()
                    return jsonify({'error': 'Session expired'}), 401
            else:
                session.clear()
                return jsonify({'error': 'Session expired'}), 401
        return f(*args, **kwargs)
    return decorated


# ── Login ────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('auth/login.html', error=request.args.get('error'))

    data = request.get_json(silent=True) or request.form
    email    = (data.get('email', '') or '').strip().lower()
    password = data.get('password', '') or ''

    if not email or not password:
        return render_template('auth/login.html', error='Email and password are required')

    try:
        result = _get_sb().auth.sign_in_with_password({'email': email, 'password': password})
        session['access_token']  = result.session.access_token
        session['refresh_token'] = result.session.refresh_token
        session['user_id']       = result.user.id
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f'[login] FAILED email={email} error={type(e).__name__}: {e}', flush=True)
        return render_template('auth/login.html', error='Invalid email or password')


# ── Signup ───────────────────────────────────────────────────────────────────

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    plan = request.args.get('plan', 'pro')

    if request.method == 'GET':
        return render_template('auth/signup.html', plan=plan)

    data     = request.get_json(silent=True) or request.form
    email    = (data.get('email', '') or '').strip().lower()
    password = data.get('password', '') or ''
    plan     = data.get('plan', plan)

    if not email or not password:
        return render_template('auth/signup.html', plan=plan, error='Email and password are required')

    if len(password) < 8:
        return render_template('auth/signup.html', plan=plan, error='Password must be at least 8 characters')

    try:
        callback_url = request.host_url.rstrip('/') + '/auth/callback'
        result = _get_sb().auth.sign_up({
            'email': email,
            'password': password,
            'options': {'email_redirect_to': callback_url},
        })
        session['pending_plan'] = plan

        if result.session:
            # Email confirmation disabled → immediate session
            session['access_token']  = result.session.access_token
            session['refresh_token'] = result.session.refresh_token
            session['user_id']       = result.user.id
            return redirect(url_for('billing.setup_trial') + f'?plan={plan}')
        else:
            # Email confirmation required → show "check your inbox" page
            session['pending_email'] = email
            return render_template('auth/confirm_email.html', email=email)
    except Exception as e:
        err = str(e)
        if 'already registered' in err.lower() or 'already exists' in err.lower():
            err = 'An account with this email already exists. Please log in.'
        return render_template('auth/signup.html', plan=plan, error=err)


# ── Forgot / Reset password ──────────────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'GET':
        return render_template('auth/forgot_password.html')

    data  = request.get_json(silent=True) or request.form
    email = (data.get('email', '') or '').strip().lower()

    if not email:
        return render_template('auth/forgot_password.html', error='Email is required')

    try:
        redirect_url = request.host_url.rstrip('/') + '/auth/reset-password'
        _get_sb().auth.reset_password_email(email, options={'redirect_to': redirect_url})
    except Exception:
        pass  # always show success to avoid email enumeration

    return render_template('auth/forgot_password.html', sent=True, email=email)


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    # Supabase redirects here with access_token in the URL fragment (#)
    # The fragment is not sent to the server — we capture it via JS and pass it as query param
    access_token = request.args.get('access_token') or request.form.get('access_token', '')

    if request.method == 'GET':
        return render_template('auth/reset_password.html', access_token=access_token)

    data     = request.get_json(silent=True) or request.form
    password = data.get('password', '')
    confirm  = data.get('confirm', '')
    token    = data.get('access_token', '')

    if not token:
        return render_template('auth/reset_password.html', error='Invalid or expired reset link. Please request a new one.', access_token='')

    if len(password) < 8:
        return render_template('auth/reset_password.html', error='Password must be at least 8 characters.', access_token=token)

    if password != confirm:
        return render_template('auth/reset_password.html', error='Passwords do not match.', access_token=token)

    try:
        sb = _get_sb()
        sb.auth.set_session(token, '')
        sb.auth.update_user({'password': password})
        return render_template('auth/reset_password.html', success=True)
    except Exception as e:
        return render_template('auth/reset_password.html', error='Reset failed. The link may have expired — request a new one.', access_token=token)


# ── Email confirmation callback (Supabase redirect) ──────────────────────────

@auth_bp.route('/callback')
def callback():
    """
    Supabase redirects here after email confirmation.
    Token arrives in URL fragment (#access_token=...) — not server-readable.
    We serve a minimal page that extracts it via JS and posts it back.
    """
    return render_template('auth/callback.html')


@auth_bp.route('/callback/complete', methods=['POST'])
def callback_complete():
    """Receives access_token extracted by JS from the URL fragment."""
    data          = request.get_json(silent=True) or {}
    access_token  = data.get('access_token', '')
    refresh_token = data.get('refresh_token', '')

    if not access_token:
        return jsonify({'error': 'Missing token'}), 400

    try:
        sb   = _get_sb()
        user = sb.auth.get_user(access_token)
        session['access_token']  = access_token
        session['refresh_token'] = refresh_token
        session['user_id']       = user.user.id
        pending_plan = session.get('pending_plan', 'pro')
        return jsonify({'redirect': f'/billing/setup-trial?plan={pending_plan}'})
    except Exception as e:
        return jsonify({'error': 'Invalid token', 'detail': str(e)}), 400


# ── Logout ───────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    try:
        _get_sb().auth.sign_out()
    except Exception:
        pass
    session.clear()
    return redirect('/')


# ── Session refresh ───────────────────────────────────────────────────────────

@auth_bp.route('/refresh', methods=['POST'])
def refresh():
    refresh_token = session.get('refresh_token')
    if not refresh_token:
        return jsonify({'error': 'No refresh token'}), 401
    try:
        result = _get_sb().auth.refresh_session(refresh_token)
        session['access_token']  = result.session.access_token
        session['refresh_token'] = result.session.refresh_token
        return jsonify({'status': 'ok'})
    except Exception:
        session.clear()
        return jsonify({'error': 'Session expired'}), 401
