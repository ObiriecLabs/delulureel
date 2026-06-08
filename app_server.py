import os
import socket

# Pre-import httpcore/httpx/supabase in the main process BEFORE gunicorn
# spawns gthread workers. Python 3.14 has a race condition in the import lock
# when multiple threads import httpcore concurrently for the first time —
# the module appears partially initialised and raises AttributeError on
# 'ConnectionPool'. Eager import here ensures sys.modules is populated once,
# so every thread just gets the cached, fully-initialised module.
import httpcore   # noqa: F401
import httpx      # noqa: F401
import supabase   # noqa: F401
# Force httpcore lazy attributes to fully initialise in the master process
# BEFORE gunicorn forks gthread workers. Python 3.14 __getattr__ lazy-load
# is not thread-safe: concurrent threads racing on first access of
# ConnectionPool raise AttributeError. Accessing here (master/preload)
# guarantees sys.modules has the fully-initialised module for all workers.
try:
    _ = httpcore.ConnectionPool
    _ = httpcore.AsyncConnectionPool
except Exception:
    pass

from flask import Flask, send_from_directory, redirect, request, session, url_for, render_template, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from core.i18n import get_lang, t as _t, SUPPORTED_LANGS, LANG_NAMES

load_dotenv()

# Hard-coded safety net: SUPABASE_URL is NOT a secret (it's a public endpoint URL).
# Render "restart" (non-redeploy) does not propagate env var changes to the running
# container — the value baked at build time is used. If the env var was missing at
# build time, os.getenv() returns '' and create_client() raises supabase_url is required.
# This setdefault ensures the URL is always available regardless of deploy state.
_SUPABASE_URL_DEFAULT = 'https://iauotqpmxsapjflnlrgn.supabase.co'
if not os.environ.get('SUPABASE_URL'):
    os.environ['SUPABASE_URL'] = _SUPABASE_URL_DEFAULT
    print(f'[startup] SUPABASE_URL was missing — applied fallback: {_SUPABASE_URL_DEFAULT}', flush=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-prod')

# Trust Cloudflare / Render reverse proxy headers so Flask knows the real
# scheme (https), host and client IP. Without this, session cookies may be
# set without Secure flag and url_for(_external=True) generates http:// URLs.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config.update(
    SESSION_COOKIE_SECURE=True,       # only send cookie over HTTPS
    SESSION_COOKIE_HTTPONLY=True,     # block JS access
    SESSION_COOKIE_SAMESITE='Lax',    # allow cross-site navigations (Stripe redirect back)
    MAX_CONTENT_LENGTH=60 * 1024 * 1024,  # 60 MB max upload (photo 10MB + audio 50MB)
)

# ── i18n context processor ──
@app.context_processor
def inject_i18n():
    lang = get_lang(session, request)
    return dict(
        t=lambda key: _t(key, lang),
        lang=lang,
        SUPPORTED_LANGS=SUPPORTED_LANGS,
        LANG_NAMES=LANG_NAMES,
    )

# ── Language switcher ──
@app.route('/set-lang', methods=['POST', 'GET'])
def set_lang():
    lang = request.form.get('lang') or request.args.get('lang', 'en')
    if lang in SUPPORTED_LANGS:
        session['lang'] = lang
    referrer = request.referrer
    return redirect(referrer if referrer else url_for('dashboard'))

# ── Blueprints ──
from saas.auth.routes import auth_bp
from saas.billing.routes import billing_bp
from saas.video.routes import video_bp
from saas.byoc.routes import byoc_bp
from saas.studio.routes import studio_bp

app.register_blueprint(auth_bp,    url_prefix='/auth')
app.register_blueprint(billing_bp, url_prefix='/billing')
app.register_blueprint(video_bp,   url_prefix='/video')
app.register_blueprint(byoc_bp,    url_prefix='/byoc')
app.register_blueprint(studio_bp,  url_prefix='/studio')

# ── Landing page (static HTML, no Jinja) ──
@app.route('/')
def index():
    return send_from_directory('landing', 'index.html')

# CTA redirects from landing
@app.route('/trial')
def trial():
    plan = request.args.get('plan', 'pro')
    return redirect(url_for('auth.signup') + f'?plan={plan}')

@app.route('/login')
def login_page():
    return redirect(url_for('auth.login'))

# Protected app routes
@app.route('/dashboard')
def dashboard():
    if not session.get('access_token'):
        return redirect(url_for('auth.login'))
    return render_template('dashboard.html')

@app.route('/upload')
def upload_page():
    if not session.get('access_token'):
        return redirect(url_for('auth.login'))
    return render_template('upload.html')

@app.route('/result/<job_id>')
def result_page(job_id):
    if not session.get('access_token'):
        return redirect(url_for('auth.login'))
    return render_template('result.html', job_id=job_id)

# Legal pages (served from landing/)
@app.route('/privacy')
def privacy():
    return send_from_directory('landing', 'privacy.html')

@app.route('/terms')
def terms():
    return send_from_directory('landing', 'terms.html')

@app.route('/contact')
def contact():
    return redirect('/')           # placeholder until contact form / email page is ready

# Public share page (no auth required)
@app.route('/share/<job_id>')
def share_page(job_id):
    return render_template('share.html', job_id=job_id)

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html', lang=get_lang(session, request)), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

# Health check (Render uses this)
@app.route('/health')
def health():
    import subprocess
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                      cwd=os.path.dirname(__file__) or '.').decode().strip()
    except Exception:
        sha = 'unknown'
    return jsonify({'status': 'ok', 'sha': sha})


import threading
from saas.video.routes import _startup_recovery, _periodic_recovery_sweep
# Startup recovery: rescues orphaned jobs after a deploy/restart.
# Periodic sweep: every 10 min, recovers interactive clips stuck in 'generating'.
# Both use a short initial delay so httpcore is fully initialised first.
threading.Timer(5.0, _startup_recovery).start()
threading.Thread(target=_periodic_recovery_sweep, daemon=True).start()


def _find_free_port(start: int = 5000, end: int = 5100) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    raise RuntimeError('No free port found in range 5000–5100')


if __name__ == '__main__':
    port = int(os.getenv('PORT', 0)) or _find_free_port()
    debug = os.getenv('FLASK_ENV', 'production') == 'development'
    print(f'🎬 DELULUREEL starting on http://localhost:{port}')
    app.run(debug=debug, port=port, host='0.0.0.0')
