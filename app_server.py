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
    return send_from_directory('landing', 'contact.html')


# ── Contact form submission ──────────────────────────────────────────────────
import time as _time
_contact_rate: dict = {}   # ip → list[timestamp]
_CONTACT_MAX = 5           # max submissions per window
_CONTACT_WIN = 3600        # window in seconds (1 hour)

@app.route('/contact/send', methods=['POST'])
def contact_send():
    import re
    import requests as _req

    # ── Honeypot check ──
    if request.form.get('hp', '').strip():
        return jsonify({'ok': True})   # silent discard for bots

    # ── Rate limit (per IP) ──
    ip  = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    now = _time.time()
    hits = [t for t in _contact_rate.get(ip, []) if now - t < _CONTACT_WIN]
    if len(hits) >= _CONTACT_MAX:
        return jsonify({'ok': False, 'error': 'Too many messages. Please try again later.'}), 429
    hits.append(now)
    _contact_rate[ip] = hits

    # ── Fields ──
    name    = (request.form.get('name',    '') or '').strip()[:120]
    email   = (request.form.get('email',   '') or '').strip()[:200]
    subject = (request.form.get('subject', '') or '').strip()[:200]
    message = (request.form.get('message', '') or '').strip()[:4000]

    if not name or not email or not subject or not message:
        return jsonify({'ok': False, 'error': 'All fields are required.'}), 400
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return jsonify({'ok': False, 'error': 'Invalid email address.'}), 400

    # ── Send via Resend REST API ──
    resend_key = os.getenv('RESEND_API_KEY', '')
    if not resend_key:
        print('[contact] RESEND_API_KEY not set — email not sent', flush=True)
        return jsonify({'ok': True})   # silent success in dev

    html_body = f"""
<div style="font-family:sans-serif;max-width:600px;color:#1a1a1a">
  <h2 style="margin:0 0 16px;font-size:20px">New contact message — DELULUREEL</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <tr><td style="padding:8px 12px;background:#f5f5f5;font-weight:600;width:100px">Name</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5">{name}</td></tr>
    <tr><td style="padding:8px 12px;background:#f5f5f5;font-weight:600">Email</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5"><a href="mailto:{email}">{email}</a></td></tr>
    <tr><td style="padding:8px 12px;background:#f5f5f5;font-weight:600">Subject</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e5e5">{subject}</td></tr>
    <tr><td style="padding:8px 12px;background:#f5f5f5;font-weight:600;vertical-align:top">Message</td>
        <td style="padding:8px 12px;white-space:pre-wrap">{message}</td></tr>
  </table>
  <p style="margin-top:20px;font-size:12px;color:#888">Sent from delulureel.com/contact — IP: {ip}</p>
</div>"""

    try:
        resp = _req.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {resend_key}',
                'Content-Type':  'application/json',
            },
            json={
                'from':     'DELULUREEL Contact <noreply@delulureel.com>',
                'to':       ['obiriec@gmail.com'],
                'reply_to': email,
                'subject':  f'[DELULUREEL] {subject} — from {name}',
                'html':     html_body,
            },
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            print(f'[contact] Resend error {resp.status_code}: {resp.text}', flush=True)
            return jsonify({'ok': False, 'error': 'Could not send your message. Please email us directly at support@delulureel.com.'}), 502
        print(f'[contact] message sent — from={email} subject={subject!r}', flush=True)
        return jsonify({'ok': True})
    except Exception as exc:
        print(f'[contact] send exception: {exc}', flush=True)
        return jsonify({'ok': False, 'error': 'Network error. Please email us directly at support@delulureel.com.'}), 502

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
import time as _bg_time
from saas.video.routes import _startup_recovery, _periodic_recovery_sweep
# Startup recovery: rescues orphaned jobs after a deploy/restart.
# Periodic sweep: every 10 min, recovers interactive clips stuck in 'generating'.
# Both use a short initial delay so httpcore is fully initialised first.
threading.Timer(5.0, _startup_recovery).start()
threading.Thread(target=_periodic_recovery_sweep, daemon=True).start()


def _runpod_health_monitor():
    """
    Monitoraggio endpoint RunPod ogni 10 min (07:00-01:00 Rome).
    Chiama solo /health — nessun job GPU, nessun costo.
    Utile per sapere se il pool ha worker attivi prima che arrivino job reali.

    NOTA COSTI: warmup GPU attivo (workersMin≥1) H200 SXM = ~$1,500/mese.
    Warmup tramite job finti = ~$195/mese per 72 ping/giorno.
    Entrambi non giustificati per il volume MVP — si accetta cold start (3-5 min).
    """
    from datetime import datetime, timezone, timedelta
    from core.comfyui_client import queue_depth

    ROME_OFFSET = timedelta(hours=2)   # CEST; adeguare a +1 in inverno se necessario
    CHECK_INTERVAL = 600               # 10 minuti

    _bg_time.sleep(30)   # lascia partire completamente il server prima di pollare
    while True:
        try:
            now_rome = datetime.now(timezone.utc) + ROME_OFFSET
            hour = now_rome.hour
            # 07:00-01:00 Rome = hour in {7..23, 0}
            in_active_hours = (7 <= hour <= 23) or (hour == 0)
            if in_active_hours:
                depth = queue_depth()
                marker = '⚠️ COLD' if depth == 0 else '✅'
                print(
                    f'[runpod] heartbeat {marker} — queue={depth} '
                    f'at {now_rome.strftime("%H:%M")} Rome',
                    flush=True,
                )
        except Exception as exc:
            print(f'[runpod] heartbeat error: {exc}', flush=True)
        _bg_time.sleep(CHECK_INTERVAL)


if os.getenv('RUNPOD_API_KEY') and os.getenv('RUNPOD_ENDPOINT'):
    threading.Thread(target=_runpod_health_monitor, daemon=True).start()


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
