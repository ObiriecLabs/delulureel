import os
import json
import uuid
import shutil
import tempfile
import threading
import requests as _requests
from datetime import date
from flask import Blueprint, request, session, jsonify, Response, stream_with_context, redirect, url_for
from supabase import create_client

from saas.auth.routes import require_auth_api

video_bp = Blueprint('video', __name__)

MAX_CONCURRENT_PER_USER = 1
MAX_CONCURRENT_GLOBAL   = int(os.getenv('MAX_CONCURRENT_GLOBAL',   10))
TRIAL_MAX_GENERATIONS   = int(os.getenv('TRIAL_MAX_GENERATIONS',   3))
DAILY_BUDGET_CAP_USD    = float(os.getenv('DAILY_BUDGET_CAP_USD',  200))

# In-memory rate-limit state (replace with Redis in production)
_active_user_jobs: dict[str, str] = {}   # user_id → job_id
_global_active    = 0
_daily: dict      = {'date': str(date.today()), 'usd': 0.0}
_lock             = threading.Lock()


def _sb_anon():
    return create_client(os.getenv('SUPABASE_URL', ''), os.getenv('SUPABASE_ANON_KEY', ''))


def _sb_service():
    return create_client(os.getenv('SUPABASE_URL', ''), os.getenv('SUPABASE_SERVICE_KEY', ''))


def _budget_ok(cost: float) -> bool:
    today = str(date.today())
    if _daily['date'] != today:
        _daily['date'] = today
        _daily['usd']  = 0.0
    return _daily['usd'] + cost <= DAILY_BUDGET_CAP_USD


def _record_spend(cost: float):
    today = str(date.today())
    if _daily['date'] != today:
        _daily['date'] = today
        _daily['usd']  = 0.0
    _daily['usd'] = round(_daily['usd'] + cost, 4)
    # Also persist to DB for multi-instance accuracy
    try:
        _sb_service().rpc('add_daily_spend', {'p_usd': cost}).execute()
    except Exception:
        pass


# ── Generate ──────────────────────────────────────────────────────────────────

@video_bp.route('/generate', methods=['POST'])
@require_auth_api
def generate():
    global _global_active
    user_id = request.current_user.id

    with _lock:
        if user_id in _active_user_jobs:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
        if _global_active >= MAX_CONCURRENT_GLOBAL:
            return jsonify({'error': 'Service at capacity. Please try again in a few minutes.'}), 429

    # Fetch profile
    sb = _sb_anon()
    try:
        prof = sb.table('profiles').select('*').eq('user_id', user_id).single().execute().data
    except Exception:
        return jsonify({'error': 'Profile not found. Please contact support.'}), 404

    # Access checks
    if prof.get('status') == 'suspended':
        return jsonify({'error': 'Account suspended. Please update your payment method.'}), 403
    if prof.get('status') in ('cancelled', 'inactive'):
        return jsonify({'error': 'No active subscription. Please start a trial.'}), 403
    if prof.get('status') == 'trial' and prof.get('trial_reels_used', 0) >= TRIAL_MAX_GENERATIONS:
        return jsonify({'error': f'Trial limit reached ({TRIAL_MAX_GENERATIONS} reels). Billing starts on Day 7.'}), 403
    if prof.get('reels_used_this_month', 0) >= prof.get('reel_limit', 5):
        return jsonify({'error': 'Monthly reel limit reached. Upgrade your plan for more.'}), 403

    # Files
    photo = request.files.get('photo')
    audio = request.files.get('audio')
    if not photo or not audio:
        return jsonify({'error': 'photo and audio files are required'}), 400

    style        = (request.form.get('style', 'cinematic') or 'cinematic').lower()
    aspect_ratio = request.form.get('aspect_ratio', '9:16') or '9:16'

    # Cost estimate (5-second Kling clip)
    from core.video_generator import estimate_cost, ENDPOINT_PRO
    est_cost = estimate_cost(5, ENDPOINT_PRO)

    if not _budget_ok(est_cost):
        return jsonify({'error': 'Service temporarily unavailable (daily budget reached).'}), 503

    # Save uploads to temp dir
    tmp_dir = tempfile.mkdtemp(prefix='dlr_')
    ext_photo = (photo.filename or 'photo.jpg').rsplit('.', 1)[-1].lower() or 'jpg'
    ext_audio = (audio.filename or 'audio.mp3').rsplit('.', 1)[-1].lower() or 'mp3'
    photo_path = os.path.join(tmp_dir, f'photo.{ext_photo}')
    audio_path = os.path.join(tmp_dir, f'audio.{ext_audio}')
    photo.save(photo_path)
    audio.save(audio_path)

    job_id = str(uuid.uuid4())

    # Persist job
    _sb_service().table('reel_jobs').insert({
        'id':            job_id,
        'user_id':       user_id,
        'status':        'queued',
        'style':         style,
        'aspect_ratio':  aspect_ratio,
        'estimated_cost': est_cost,
    }).execute()

    # Lock slots
    with _lock:
        _active_user_jobs[user_id] = job_id
        _global_active += 1

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio, est_cost, tmp_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id, 'status': 'queued'})


# ── Background pipeline ───────────────────────────────────────────────────────

def _run_pipeline(job_id, user_id, photo_path, audio_path, style, aspect_ratio, est_cost, tmp_dir):
    global _global_active
    sb = _sb_service()

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        # 1 — Audio analysis
        update('analyzing')
        from core.audio_analyzer import analyze_audio
        analysis = analyze_audio(audio_path)

        # 2 — Scene prompt (Claude)
        update('generating', bpm=analysis['bpm'])
        from core.scene_director import generate_scene_prompt
        prompt = generate_scene_prompt(analysis, style)

        # 3 — Upload photo to Supabase Storage (private bucket → signed URL for fal.ai)
        storage_key = f'jobs/{job_id}/source.jpg'
        with open(photo_path, 'rb') as fh:
            sb.storage.from_('reel-uploads').upload(
                storage_key, fh.read(),
                file_options={'content-type': 'image/jpeg'},
            )
        signed     = sb.storage.from_('reel-uploads').create_signed_url(storage_key, 3600)
        photo_url  = signed.get('signedURL') or signed.get('signed_url') or signed.get('data', {}).get('signedUrl', '')

        # 4 — Submit to fal.ai (Kling 3.0 Pro)
        update('processing', fal_request_id='pending', prompt=prompt)
        from core.video_generator import submit_reel, poll_until_done
        fal = submit_reel(photo_url, prompt, duration=5, aspect_ratio=aspect_ratio)
        update('processing', fal_request_id=fal['request_id'], fal_endpoint=fal['endpoint'])

        # 5 — Poll fal.ai until done
        raw_video_url = poll_until_done(fal['request_id'], fal['endpoint'])

        # 6 — Download raw video
        raw_path = os.path.join(tmp_dir, 'raw.mp4')
        resp = _requests.get(raw_video_url, stream=True, timeout=180)
        resp.raise_for_status()
        with open(raw_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)

        # 7 — Assemble (FFmpeg: raw video + original audio)
        ar_slug    = aspect_ratio.replace(':', 'x')
        final_path = os.path.join(tmp_dir, f'reel_{ar_slug}.mp4')
        from core.assembler import assemble_reel
        assemble_reel([raw_path], audio_path, final_path, aspect_ratio)

        # 8 — Upload final reel
        output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'
        with open(final_path, 'rb') as fh:
            sb.storage.from_('reel-outputs').upload(
                output_key, fh.read(),
                file_options={'content-type': 'video/mp4'},
            )
        final_url = sb.storage.from_('reel-outputs').get_public_url(output_key)

        # 9 — Mark completed
        _record_spend(est_cost)
        update('completed', output_url=final_url, actual_cost=est_cost)

        # 10 — Increment reel counter
        sb.rpc('increment_reel_count', {'p_user_id': user_id}).execute()

    except Exception as exc:
        update('failed', error_message=str(exc)[:500])

    finally:
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ── Status & SSE ──────────────────────────────────────────────────────────────

@video_bp.route('/status/<job_id>')
@require_auth_api
def status(job_id):
    user_id = request.current_user.id
    sb = _sb_anon()
    try:
        job = sb.table('reel_jobs').select(
            'id,status,output_url,error_message,bpm,prompt,created_at,style,aspect_ratio'
        ).eq('id', job_id).eq('user_id', user_id).single().execute()
        return jsonify(job.data)
    except Exception:
        return jsonify({'error': 'Job not found'}), 404


@video_bp.route('/status/<job_id>/stream')
@require_auth_api
def status_stream(job_id):
    user_id = request.current_user.id

    def _gen():
        import time
        sb = _sb_anon()
        for _ in range(80):  # max ~13 min at 10s interval
            try:
                job = sb.table('reel_jobs').select(
                    'status,output_url,error_message,bpm'
                ).eq('id', job_id).eq('user_id', user_id).single().execute()
                data = job.data
                yield f'data: {json.dumps(data)}\n\n'
                if data.get('status') in ('completed', 'failed'):
                    break
            except Exception as e:
                yield f'data: {json.dumps({"error": str(e)})}\n\n'
                break
            time.sleep(10)

    return Response(
        stream_with_context(_gen()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── History ───────────────────────────────────────────────────────────────────

@video_bp.route('/history')
@require_auth_api
def history():
    user_id = request.current_user.id
    sb = _sb_anon()
    jobs = (
        sb.table('reel_jobs')
        .select('id,status,output_url,created_at,style,aspect_ratio,bpm')
        .eq('user_id', user_id)
        .order('created_at', desc=True)
        .limit(20)
        .execute()
    )
    return jsonify(jobs.data)


# ── Profile info ──────────────────────────────────────────────────────────────

@video_bp.route('/profile')
@require_auth_api
def profile():
    user_id = request.current_user.id
    sb = _sb_anon()
    try:
        prof = sb.table('profiles').select(
            'plan,status,reel_limit,reels_used_this_month,trial_reels_used'
        ).eq('user_id', user_id).single().execute()
        return jsonify(prof.data)
    except Exception:
        return jsonify({'plan': None, 'status': 'inactive'}), 200
