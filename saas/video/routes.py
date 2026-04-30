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

# Base URL for webhook (must be externally reachable — set APP_BASE_URL in Render env)
APP_BASE_URL = os.getenv('APP_BASE_URL', 'https://delulureel.com').rstrip('/')

# In-memory rate-limit state (replace with Redis in production)
_active_user_jobs: dict[str, str] = {}   # user_id → job_id
_global_active    = 0
_daily: dict      = {'date': str(date.today()), 'usd': 0.0}
_lock             = threading.Lock()

# Webhook tracking: fal request_id → job_id (for single-clip webhook lookup)
_fal_req_to_job: dict[str, str] = {}
_webhook_lock = threading.Lock()


_sb_svc = None

def _sb_service():
    global _sb_svc
    if _sb_svc is None:
        _sb_svc = create_client(os.getenv('SUPABASE_URL', ''), os.getenv('SUPABASE_SERVICE_KEY', ''))
    return _sb_svc


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


# ── Startup recovery ─────────────────────────────────────────────────────────

def _startup_recovery():
    """On startup: recover jobs where fal.ai completed but post-gen was killed.

    For each job stuck in processing/queued/analyzing/generating:
    - If it has a valid single-clip fal_request_id: fetch the completed result
      from fal.ai and restart _run_post_generation.
    - Otherwise: mark failed (unrecoverable — e.g. multi-clip, pre-gen killed).

    Called from app_server.py after blueprint registration.
    """
    global _global_active
    try:
        import fal_client as _fal
        sb = _sb_service()

        result = sb.table('reel_jobs').select(
            'id,user_id,fal_request_id,fal_endpoint,aspect_ratio,estimated_cost'
        ).in_('status', ['processing', 'queued', 'analyzing', 'generating']).execute()

        rows = result.data or []
        if not rows:
            return

        failed_ids = []
        recovered  = 0

        for job in rows:
            req_id   = (job.get('fal_request_id') or '').strip()
            endpoint = (job.get('fal_endpoint') or 'fal-ai/kling-video/v2.6/pro/image-to-video')
            job_id   = job['id']
            user_id  = job['user_id']

            # Multi-clip, pre-gen killed, or no fal_request_id — unrecoverable
            if not req_id or req_id.startswith('multi:') or req_id == 'pending':
                failed_ids.append(job_id)
                continue

            # Try to fetch the completed result from fal.ai
            try:
                fal_result = _fal.result(endpoint, req_id)
                video_url  = ((fal_result.get('video') or {}).get('url')
                              or fal_result.get('video_url') or '')
                if not video_url:
                    failed_ids.append(job_id)
                    continue

                # fal.ai job was already done — restart post-generation
                with _lock:
                    _active_user_jobs[user_id] = job_id
                    _global_active += 1

                threading.Thread(
                    target=_run_post_generation,
                    args=(job_id, user_id, video_url,
                          job.get('aspect_ratio', '9:16'),
                          float(job.get('estimated_cost') or 0)),
                    daemon=True,
                ).start()
                recovered += 1
                print(f'🔄  Recovering job {job_id[:8]}... (fal.ai result retrieved)')

            except Exception:
                # Not found / expired / still running — unrecoverable
                failed_ids.append(job_id)

        if failed_ids:
            sb.table('reel_jobs').update({
                'status':        'failed',
                'error_message': 'Server restarted during processing. Please retry.',
            }).in_('id', failed_ids).execute()
            print(f'⚠️  Marked {len(failed_ids)} unrecoverable orphaned job(s) as failed')

        if recovered:
            print(f'🔄  Recovered {recovered} orphaned job(s) — post-generation resumed')

    except Exception as e:
        print(f'⚠️  Startup recovery failed (non-critical): {e}')


# ── Generate ──────────────────────────────────────────────────────────────────

@video_bp.route('/generate', methods=['POST'])
@require_auth_api
def generate():
    import time as _time
    _t0 = _time.time()
    global _global_active
    user_id = request.current_user.id
    print(f'[generate] START user={user_id[:8]}')

    with _lock:
        if user_id in _active_user_jobs:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
        if _global_active >= MAX_CONCURRENT_GLOBAL:
            return jsonify({'error': 'Service at capacity. Please try again in a few minutes.'}), 429

    # Fetch profile
    print(f'[generate] fetching profile ({_time.time()-_t0:.1f}s)')
    sb = _sb_service()
    try:
        prof = sb.table('profiles').select('*').eq('user_id', user_id).single().execute().data
        print(f'[generate] profile OK ({_time.time()-_t0:.1f}s) status={prof.get("status")}')
    except Exception as e:
        print(f'[generate] profile FAILED ({_time.time()-_t0:.1f}s): {e}')
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
    print(f'[generate] saving files ({_time.time()-_t0:.1f}s)')
    photo.save(photo_path)
    audio.save(audio_path)
    print(f'[generate] files saved ({_time.time()-_t0:.1f}s)')

    job_id = str(uuid.uuid4())

    # Persist job
    print(f'[generate] inserting job ({_time.time()-_t0:.1f}s)')
    _sb_service().table('reel_jobs').insert({
        'id':             job_id,
        'user_id':        user_id,
        'status':         'queued',
        'style':          style,
        'aspect_ratio':   aspect_ratio,
        'estimated_cost': est_cost,
    }).execute()

    print(f'[generate] job inserted ({_time.time()-_t0:.1f}s)')

    # Lock slots
    with _lock:
        _active_user_jobs[user_id] = job_id
        _global_active += 1

    # ── Dispatch ─────────────────────────────────────────────────────────────
    # Single clip → webhook-based (fal.ai generates, notifies us when done)
    # Multi-clip  → polling (N parallel jobs, harder to webhook-aggregate)
    if n_clips == 1:
        thread = threading.Thread(
            target=_run_pre_generation,
            args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  est_cost, tmp_dir, target_secs, ext_audio),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_run_pipeline,
            args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  est_cost, tmp_dir, target_secs, n_clips),
            daemon=True,
        )
    thread.start()

    print(f'[generate] DONE returning job_id ({_time.time()-_t0:.1f}s)')
    return jsonify({
        'job_id':       job_id,
        'status':       'queued',
        'target_secs':  target_secs,
        'n_clips':      n_clips,
    })


# ── Phase 1: Pre-generation (single clip, webhook mode) ───────────────────────
# Short-lived thread (~30s): audio analysis + Claude + uploads + fal.ai submit
# Thread ends as soon as fal.ai acknowledges the job. Generation happens on
# fal.ai servers — our server is idle until the webhook fires.

def _run_pre_generation(job_id, user_id, photo_path, audio_path, style,
                        aspect_ratio, est_cost, tmp_dir, target_secs, ext_audio):
    global _global_active
    sb = _sb_service()

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        # 1 — Audio analysis
        update('analyzing')
        from core.audio_analyzer import analyze_audio
        analysis = analyze_audio(audio_path)
        gc.collect()

        # 2 — Scene prompt (Claude)
        update('generating', bpm=analysis['bpm'])
        from core.scene_director import generate_scene_prompt
        prompt = generate_scene_prompt(analysis, style)

        # 3 — Upload photo to fal.ai CDN (accessible from fal.ai workers)
        import fal_client as _fal
        try:
            photo_url = _fal.upload_file(photo_path)
        except Exception as exc:
            raise RuntimeError(f'fal upload_file failed: {exc}') from exc
        if not photo_url or not isinstance(photo_url, str):
            raise RuntimeError(f'upload_file returned invalid URL: {photo_url!r:.80}')

        # Archive photo to Supabase (fire-and-forget)
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    f'jobs/{job_id}/source.jpg', fh.read(),
                    file_options={'content-type': 'image/jpeg'},
                )
        except Exception:
            pass

        # 4 — Upload audio to Supabase Storage
        # The post-generation webhook handler runs in a fresh thread with no
        # access to the local tmp_dir, so the audio must be persisted in Supabase.
        audio_key = f'jobs/{job_id}/audio.{ext_audio}'
        try:
            with open(audio_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    audio_key, fh,
                    file_options={'content-type': f'audio/{ext_audio}'},
                )
        except Exception as exc:
            raise RuntimeError(f'Audio upload to Supabase failed: {exc}') from exc

        # 5 — Submit to fal.ai WITH webhook URL
        # fal.ai will call POST /video/webhook/fal when generation is complete.
        from core.video_generator import submit_reel, ENDPOINT_PRO
        clip_len    = min(target_secs, 10)
        webhook_url = f'{APP_BASE_URL}/video/webhook/fal'

        try:
            fal = submit_reel(
                photo_url, prompt,
                duration=clip_len, aspect_ratio=aspect_ratio,
                endpoint=ENDPOINT_PRO, webhook_url=webhook_url,
            )
        except Exception as exc:
            raise RuntimeError(
                f'fal submit failed [ep={ENDPOINT_PRO}, dur={clip_len}]: {exc}'
            ) from exc

        # Register request_id so the webhook handler can find this job
        with _webhook_lock:
            _fal_req_to_job[fal['request_id']] = job_id

        update('processing',
               fal_request_id=fal['request_id'],
               fal_endpoint=fal['endpoint'],
               prompt=prompt)

        # Thread ends here. fal.ai is generating the video on their servers.
        # Execution resumes in _run_post_generation when the webhook fires.

    except Exception as exc:
        update('failed', error_message=str(exc)[:500])
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)

    finally:
        # Local files no longer needed — audio is now on Supabase
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ── Webhook: fal.ai notifies us when generation is complete ───────────────────

@video_bp.route('/webhook/fal', methods=['POST'])
def fal_webhook():
    """
    fal.ai calls this endpoint when a submitted job finishes (OK or ERROR).
    We must return 200 quickly — fal.ai retries on non-200.
    Heavy work (download, FFmpeg, upload) is offloaded to _run_post_generation.
    """
    data = request.get_json(silent=True) or {}

    # fal.ai payload shape: {"request_id": "...", "status": "OK"|"ERROR", "payload": {...}}
    req_id = data.get('request_id') or (data.get('request') or {}).get('id') or ''
    status = data.get('status', '')

    if not req_id:
        return jsonify({'ok': False, 'reason': 'missing request_id'}), 400

    # Look up job_id from in-memory map
    with _webhook_lock:
        job_id = _fal_req_to_job.pop(req_id, None)

    if not job_id:
        # Could be a late webhook after server restart — try DB lookup by fal_request_id
        try:
            sb = _sb_service()
            rows = sb.table('reel_jobs').select('id,user_id,status,aspect_ratio,estimated_cost') \
                     .eq('fal_request_id', req_id).limit(1).execute()
            if rows.data:
                job_id = rows.data[0]['id']
            else:
                return jsonify({'ok': True, 'note': 'unknown request_id'}), 200
        except Exception:
            return jsonify({'ok': True, 'note': 'db lookup failed'}), 200

    sb = _sb_service()
    try:
        job = sb.table('reel_jobs').select(
            'id,user_id,status,aspect_ratio,estimated_cost,fal_endpoint'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'ok': True, 'note': 'job not found'}), 200

    # Idempotency guard
    if job.get('status') in ('completed', 'failed'):
        return jsonify({'ok': True}), 200

    user_id  = job['user_id']
    est_cost = float(job.get('estimated_cost') or 0)
    ar       = job.get('aspect_ratio', '9:16')
    endpoint = job.get('fal_endpoint', '')

    if status == 'ERROR':
        err_msg = str(data.get('error', 'fal.ai generation failed'))[:500]
        sb.table('reel_jobs').update({'status': 'failed', 'error_message': err_msg}) \
          .eq('id', job_id).execute()
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        return jsonify({'ok': True}), 200

    if status == 'OK':
        payload   = data.get('payload') or {}
        video_obj = payload.get('video') or {}
        video_url = video_obj.get('url') or payload.get('video_url') or ''

        if not video_url:
            sb.table('reel_jobs').update({
                'status': 'failed',
                'error_message': 'Webhook: no video URL in fal.ai payload',
            }).eq('id', job_id).execute()
            with _lock:
                _active_user_jobs.pop(user_id, None)
                _global_active = max(0, _global_active - 1)
            return jsonify({'ok': True}), 200

        # Spawn post-generation thread (download + FFmpeg + Supabase upload)
        thread = threading.Thread(
            target=_run_post_generation,
            args=(job_id, user_id, video_url, ar, est_cost),
            daemon=True,
        )
        thread.start()
        return jsonify({'ok': True}), 200

    # Unknown status — return 200 to avoid fal.ai retrying indefinitely
    return jsonify({'ok': True, 'note': f'unrecognised status: {status}'}), 200


# ── Phase 2: Post-generation (single clip, after webhook) ─────────────────────
# Short-lived thread (~60s): download raw video + FFmpeg + upload to Supabase

def _run_post_generation(job_id, user_id, raw_video_url, aspect_ratio, est_cost):
    global _global_active
    sb   = _sb_service()
    tmp  = tempfile.mkdtemp(prefix='dlr_post_')

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        ar_slug    = aspect_ratio.replace(':', 'x')
        raw_path   = os.path.join(tmp, 'raw.mp4')
        audio_path = os.path.join(tmp, 'audio')
        final_path = os.path.join(tmp, f'reel_{ar_slug}.mp4')

        # 1 — Download raw video from fal.ai CDN
        resp = _requests.get(raw_video_url, stream=True, timeout=180)
        resp.raise_for_status()
        with open(raw_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)

        # 2 — Download audio from Supabase Storage
        # List the jobs/{job_id}/ folder to find the audio file
        try:
            files = sb.storage.from_('reel-uploads').list(f'jobs/{job_id}')
            audio_file = next(
                (f for f in files if f.get('name', '').startswith('audio.')), None
            )
            if not audio_file:
                raise RuntimeError('Audio file not found in Supabase Storage')
            audio_key = f'jobs/{job_id}/{audio_file["name"]}'
            ext_audio = audio_file['name'].rsplit('.', 1)[-1]
            audio_path = os.path.join(tmp, f'audio.{ext_audio}')

            signed = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 3600)
            audio_signed_url = signed.get('signedURL') or signed.get('signedUrl') or ''
            if not audio_signed_url:
                raise RuntimeError(f'Could not get signed URL for audio: {signed}')

            resp_audio = _requests.get(audio_signed_url, stream=True, timeout=120)
            resp_audio.raise_for_status()
            with open(audio_path, 'wb') as fh:
                for chunk in resp_audio.iter_content(chunk_size=65536):
                    fh.write(chunk)
        except Exception as exc:
            raise RuntimeError(f'Audio retrieval failed: {exc}') from exc

        # 3 — FFmpeg assembly (raw video + original audio)
        gc.collect()
        from core.assembler import assemble_reel
        assemble_reel([raw_path], audio_path, final_path, aspect_ratio)
        gc.collect()

        # 4 — Upload final reel to Supabase (streaming — no fh.read() in RAM)
        output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'
        with open(final_path, 'rb') as fh:
            sb.storage.from_('reel-outputs').upload(
                output_key, fh,
                file_options={'content-type': 'video/mp4'},
            )
        final_url = sb.storage.from_('reel-outputs').get_public_url(output_key)

        # 5 — Mark completed
        _record_spend(est_cost)
        update('completed', output_url=final_url, actual_cost=est_cost)

        # 6 — Increment reel counter
        sb.rpc('increment_reel_count', {'p_user_id': user_id}).execute()

    except Exception as exc:
        update('failed', error_message=str(exc)[:500])

    finally:
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


# ── Multi-clip pipeline (polling, unchanged) ──────────────────────────────────
# Used for 30s / Full Track (n_clips > 1). Polling is acceptable here because
# the assembly requires all N clips before FFmpeg can run.

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
        gc.collect()

        # 2 — Scene prompt (Claude)
        update('generating', bpm=analysis['bpm'])
        from core.scene_director import generate_scene_prompt
        prompt = generate_scene_prompt(analysis, style)

        # 3 — Upload photo to fal.ai CDN
        import fal_client as _fal
        try:
            photo_url = _fal.upload_file(photo_path)
        except Exception as upload_exc:
            raise RuntimeError(f'upload_file failed: {upload_exc}') from upload_exc
        if not photo_url or not isinstance(photo_url, str):
            raise RuntimeError(f'upload_file returned invalid URL: {photo_url!r:.80}')

        # Archive photo (fire-and-forget)
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    f'jobs/{job_id}/source.jpg', fh.read(),
                    file_options={'content-type': 'image/jpeg'},
                )
        except Exception:
            pass

        # 4 — Submit multi-clip to fal.ai (parallel, polling)
        from core.video_generator import (
            submit_multi_reel, poll_until_done,
            ENDPOINT_TURBO, MAX_WAIT_MULTI,
        )

        ar_slug    = aspect_ratio.replace(':', 'x')
        final_path = os.path.join(tmp_dir, f'reel_{ar_slug}.mp4')

        update('processing',
               fal_request_id=f'multi:{n_clips}',
               fal_endpoint=ENDPOINT_TURBO,
               prompt=prompt)
        try:
            handles = submit_multi_reel(photo_url, prompt, n_clips, aspect_ratio=aspect_ratio)
        except Exception as exc:
            raise RuntimeError(
                f'fal multi-submit failed [n={n_clips}, ar={aspect_ratio}]: {exc}'
            ) from exc

        video_clips = []
        for i, h in enumerate(handles):
            try:
                url = poll_until_done(h['request_id'], h['endpoint'], MAX_WAIT_MULTI)
            except Exception as exc:
                raise RuntimeError(
                    f'poll_until_done failed [clip={i}, req={h["request_id"]}]: {exc}'
                ) from exc
            video_clips.append(download_video(url, f'clip_{i}.mp4'))

        # 5 — Assemble
        gc.collect()
        from core.assembler import assemble_reel
        assemble_reel(video_clips, audio_path, final_path, aspect_ratio,
                      max_duration=float(target_secs))
        gc.collect()

        # 6 — Upload final reel
        output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'
        with open(final_path, 'rb') as fh:
            sb.storage.from_('reel-outputs').upload(
                output_key, fh,
                file_options={'content-type': 'video/mp4'},
            )
        final_url = sb.storage.from_('reel-outputs').get_public_url(output_key)

        # 7 — Mark completed
        _record_spend(est_cost)
        update('completed', output_url=final_url, actual_cost=est_cost)
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
