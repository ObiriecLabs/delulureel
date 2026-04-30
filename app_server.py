import os
import socket
from flask import Flask, send_from_directory, redirect, request, session, url_for, render_template, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-prod')

# ── Blueprints ──
from saas.auth.routes import auth_bp
from saas.billing.routes import billing_bp
from saas.video.routes import video_bp

app.register_blueprint(auth_bp,    url_prefix='/auth')
app.register_blueprint(billing_bp, url_prefix='/billing')
app.register_blueprint(video_bp,   url_prefix='/video')

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

# Health check (Render uses this)
@app.route('/health')
def health():
    import subprocess
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                      cwd=os.path.dirname(__file__) or '.').decode().strip()
    except Exception:
        sha = 'unknown'
    return jsonify({'status': 'ok', 'version': 'baaa1bd', 'sha': sha, 'fal_client': '1.0.0'})


from saas.video.routes import _startup_recovery
_startup_recovery()


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
