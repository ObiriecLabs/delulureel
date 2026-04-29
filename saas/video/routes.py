import os
import gc
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
    sb = _sb_service()
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

    # ── Duration / clip count ────────────────────────────────────────────────
    from core.video_generator import (
        estimate_cost, endpoint_for_duration,
        n_clips_for_duration, CLIP_LEN_MULTI, MAX_AUDIO_SEC,
        ENDPOINT_PRO, ENDPOINT_TURBO,
    )

    video_duration = (request.form.get('video_duration', '10') or '10').strip()

    try:
        audio_dur_sec = max(0.0, float(request.form.get('audio_duration_sec', '0') or '0'))
    except ValueError:
        audio_dur_sec = 0.0

    if video_duration == 'full':
        target_secs = min(int(audio_dur_sec), MAX_AUDIO_SEC) if audio_dur_sec > 0 else 10
    elif video_duration in ('5', '10', '30'):
        target_secs = int(video_duration)
    else:
        target_secs = 10

    endpoint = endpoint_for_duration(target_secs)
    n_clips  = n_clips_for_duration(target_secs) if target_secs > 10 else 1
    est_cost = estimate_cost(target_secs, endpoint)

    if not _budget_ok(est_cost):
        return jsonify({'error': 'Service temporarily unavailable (daily budget reached).'}), 503

    # Save uploads to temp dir
    tmp_dir   = tempfile.mkdtemp(prefix='dlr_')
    ext_photo = (photo.filename or 'photo.jpg').rsplit('.', 1)[-1].lower() or 'jpg'
    ext_audio = (audio.filename or 'audio.mp3').rsplit('.', 1)[-1].lower() or 'mp3'
    photo_path = os.path.join(tmp_dir, f'photo.{ext_photo}')
    audio_path = os.path.join(tmp_dir, f'audio.{ext_audio}')
    photo.save(photo_path)
    audio.save(audio_path)

    job_id = str(uuid.uuid4())

    # Persist job
    _sb_service().table('reel_jobs').insert({
        'id':             job_id,
        'user_id':        user_id,
        'status':         'queued',
        'style':          style,
        'aspect_ratio':   aspect_ratio,
        'estimated_cost': est_cost,
    }).execute()

    # Lock slots
    with _lock:
        _active_user_jobs[user_id] = job_id
        _global_active += 1

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
              est_cost, tmp_dir, target_secs, n_clips),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'job_id':       job_id,
        'status':       'queued',
        'target_secs':  target_secs,
        'n_clips':      n_clips,
    })


# ── Background pipeline ───────────────────────────────────────────────────────

def _run_pipeline(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  est_cost, tmp_dir, target_secs=10, n_clips=1):
    global _global_active
    sb = _sb_service()

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    def download_video(url: str, filename: str) -> str:
        path = os.path.join(tmp_dir, filename)
        resp = _requests.get(url, stream=True, timeout=180)
        resp.raise_for_status()
        with open(path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        return path

    try:
        # 1 — Audio analysis
        update('analyzing')
        from core.audio_analyzer import analyze_audio
        analysis = analyze_audio(audio_path)
        gc.collect()   # free numpy arrays immediately after analysis

        # 2 — Scene prompt (Claude)
        update('generating', bpm=analysis['bpm'])
        from core.scene_director import generate_scene_prompt
        prompt = generate_scene_prompt(analysis, style)

        # 3 — Upload photo directly to fal.ai CDN (guarantees accessibility from fal.ai workers)
        import fal_client as _fal
        photo_url = _fal.upload_file(photo_path)
        if not photo_url:
            raise RuntimeError('fal_client.upload_file() returned empty URL')

        # Archive photo to Supabase Storage for our records (fire-and-forget)
        try:
            storage_key = f'jobs/{job_id}/source.jpg'
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    storage_key, fh.read(),
                    file_options={'content-type': 'image/jpeg'},
                )
        except Exception:
            pass  # non-critical — generation continues regardless

        # 4 — Submit to fal.ai
        from core.video_generator import (
            submit_reel, submit_multi_reel, poll_until_done,
            ENDPOINT_PRO, ENDPOINT_TURBO,
            MAX_WAIT_SINGLE, MAX_WAIT_MULTI,
        )

        ar_slug    = aspect_ratio.replace(':', 'x')
        final_path = os.path.join(tmp_dir, f'reel_{ar_slug}.mp4')

        if n_clips == 1:
            # ── Single clip (Kling 3.0 Pro) ───────────────────────────────────
            clip_len = min(target_secs, 10)
            update('processing', fal_request_id='pending', prompt=prompt)
            fal = submit_reel(
                photo_url, prompt,
                duration=clip_len, aspect_ratio=aspect_ratio, endpoint=ENDPOINT_PRO,
            )
            update('processing', fal_request_id=fal['request_id'], fal_endpoint=fal['endpoint'])
            raw_url  = poll_until_done(fal['request_id'], fal['endpoint'], MAX_WAIT_SINGLE)
            raw_path = download_video(raw_url, 'raw_0.mp4')
            video_clips = [raw_path]

        else:
            # ── Multi-clip (Kling 2.5 Turbo, parallel submit) ─────────────────
            update('processing',
                   fal_request_id=f'multi:{n_clips}',
                   fal_endpoint=ENDPOINT_TURBO,
                   prompt=prompt)
            handles = submit_multi_reel(
                photo_url, prompt, n_clips,
                aspect_ratio=aspect_ratio,
            )
            video_clips = []
            for i, h in enumerate(handles):
                url  = poll_until_done(h['request_id'], h['endpoint'], MAX_WAIT_MULTI)
                path = download_video(url, f'clip_{i}.mp4')
                video_clips.append(path)

        # 5 — Assemble (FFmpeg: clips + original audio, trimmed to target_secs)
        gc.collect()   # free download buffers before FFmpeg
        from core.assembler import assemble_reel
        assemble_reel(video_clips, audio_path, final_path, aspect_ratio,
                      max_duration=float(target_secs))
        gc.collect()   # free after FFmpeg

        # 6 — Upload final reel
        output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'
        with open(final_path, 'rb') as fh:
            sb.storage.from_('reel-outputs').upload(
                output_key, fh.read(),
                file_options={'content-type': 'video/mp4'},
            )
        final_url = sb.storage.from_('reel-outputs').get_public_url(output_key)

        # 7 — Mark completed
        _record_spend(est_cost)
        update('completed', output_url=final_url, actual_cost=est_cost)

        # 8 — Increment reel counter
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
    sb = _sb_service()
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
        sb = _sb_service()
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
    sb = _sb_service()
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
    sb = _sb_service()
    try:
        prof = sb.table('profiles').select(
            'plan,status,reel_limit,reels_used_this_month,trial_reels_used'
        ).eq('user_id', user_id).single().execute()
        return jsonify(prof.data)
    except Exception:
        return jsonify({'plan': None, 'status': 'inactive'}), 200
