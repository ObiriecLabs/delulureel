import os
import gc
import json
import math
import uuid
import shutil
import tempfile
import threading
import requests as _requests
from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, request, session, jsonify, Response, stream_with_context, redirect, url_for
from supabase import create_client, ClientOptions

from core.video_generator import (
    submit_reel, submit_multi_reel, poll_until_done,
    fal_result, transcribe_audio_fal,
    estimate_cost, endpoint_for_duration, n_clips_for_duration,
    CLIP_LEN_MULTI, MAX_AUDIO_SEC, MAX_WAIT_MULTI,
    ENDPOINT_PRO, ENDPOINT_TURBO,
)
from core.audio_analyzer import analyze_audio, beat_cut_durations
from core.scene_director import generate_scene_prompt
from core.lipsync import apply_lipsync
from core.assembler import assemble_reel

from saas.auth.routes import require_auth_api

video_bp = Blueprint('video', __name__)

MAX_CONCURRENT_PER_USER = 1
MAX_CONCURRENT_GLOBAL   = int(os.getenv('MAX_CONCURRENT_GLOBAL',  10))
TRIAL_MAX_CREDITS       = int(os.getenv('TRIAL_MAX_CREDITS',       6))   # 6 crediti ≈ 1 reel 30s o 3 reel 10s
DAILY_BUDGET_CAP_USD    = float(os.getenv('DAILY_BUDGET_CAP_USD', 200))

def _is_admin(user) -> bool:
    """True if user email is in ADMIN_EMAILS env var.
    Reads env var at call-time (not import-time) so Render env updates
    take effect without a redeploy. Handles both User objects and dicts."""
    admin_emails = {
        e.strip().lower()
        for e in os.getenv('ADMIN_EMAILS', '').split(',')
        if e.strip()
    }
    if isinstance(user, dict):
        email = (user.get('email') or '').lower()
    else:
        email = (getattr(user, 'email', None) or '').lower()
    result = bool(email and email in admin_emails)
    print(f'[admin_check] email={email!r} admin_set={admin_emails} → {result}', flush=True)
    return result


def _credits_for_duration(target_secs: int) -> int:
    """Calcola i crediti da scalare: 1 credito = 5 secondi di video generato (minimo 1)."""
    return max(1, math.ceil(target_secs / 5))

# Base URL for webhook (must be externally reachable — set APP_BASE_URL in Render env)
APP_BASE_URL = os.getenv('APP_BASE_URL', 'https://delulureel.com').rstrip('/')

# In-memory rate-limit state (replace with Redis in production)
_active_user_jobs: dict[str, str] = {}   # user_id → job_id
_global_active    = 0
_daily: dict      = {'date': str(date.today()), 'usd': 0.0}
_lock             = threading.Lock()

# Webhook tracking: fal request_id → job_id (single-clip, same-instance fast path)
# enable_lipsync / is_admin / target_secs are read from DB in the webhook handler
# so they work correctly even when the webhook arrives on a different Render instance.
_fal_req_to_job: dict[str, str] = {}
_webhook_lock = threading.Lock()

# (multi-clip state is now tracked entirely in Supabase via add_clip_result RPC)


# Per-thread Supabase service client — same thread-safety rationale as auth/routes.py.
# supabase>=2.0.0 / httpx.Client is NOT safe to share across gunicorn gthread workers.
_sb_svc_local: threading.local = threading.local()

def _sb_service():
    if not getattr(_sb_svc_local, 'svc', None):
        _sb_svc_local.svc = create_client(
            os.getenv('SUPABASE_URL', ''),
            os.getenv('SUPABASE_SERVICE_KEY', ''),
            options=ClientOptions(
                postgrest_client_timeout=10,
                storage_client_timeout=30,  # storage uploads need more time
            ),
        )
    return _sb_svc_local.svc


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
        sb = _sb_service()

        # Only recover jobs older than 60 s — prevents killing brand-new jobs
        # submitted right after a deploy while startup_recovery is still running.
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

        result = sb.table('reel_jobs').select(
            'id,user_id,fal_request_id,fal_endpoint,aspect_ratio,estimated_cost,'
            'enable_lipsync,target_secs_requested'
        ).in_('status', ['processing', 'queued', 'analyzing', 'generating', 'lipsyncing']).lt('created_at', cutoff).execute()

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
                result_data = fal_result(endpoint, req_id)
                video_url   = ((result_data.get('video') or {}).get('url')
                               or result_data.get('video_url') or '')
                if not video_url:
                    # fal.ai returned something but no URL — genuine failure
                    failed_ids.append(job_id)
                    continue

                # fal.ai job was already done — restart post-generation.
                # Fetch enable_lipsync, target_secs, is_admin from DB so the
                # recovered job behaves identically to the original run.
                enable_lipsync_r = bool(job.get('enable_lipsync', False))
                target_secs_r    = int(job.get('target_secs_requested') or 10)
                try:
                    prof_r       = sb.table('profiles').select('is_admin').eq('user_id', user_id).single().execute().data
                    is_admin_r   = bool((prof_r or {}).get('is_admin', False))
                except Exception:
                    is_admin_r   = False

                with _lock:
                    _active_user_jobs[user_id] = job_id
                    _global_active += 1

                threading.Thread(
                    target=_run_post_generation,
                    args=(job_id, user_id, video_url,
                          job.get('aspect_ratio', '9:16'),
                          float(job.get('estimated_cost') or 0)),
                    kwargs={
                        'enable_lipsync': enable_lipsync_r,
                        'target_secs':    target_secs_r,
                        'is_admin':       is_admin_r,
                    },
                    daemon=True,
                ).start()
                recovered += 1
                print(f'🔄  Recovering job {job_id[:8]}... (fal.ai result retrieved)')

            except Exception:
                # fal_result() raised — two cases:
                # a) Job still running on fal.ai (<15 min old): leave in 'processing',
                #    the webhook will arrive and _run_post_generation will handle it.
                # b) Job very old (>15 min) and fal.ai has no result: genuine failure.
                created_str = job.get('created_at', '')
                try:
                    created_dt = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                    age_minutes = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
                except Exception:
                    age_minutes = 0

                if age_minutes > 15:
                    failed_ids.append(job_id)
                    print(f'⚠️  Job {job_id[:8]} has fal_request_id but is >{age_minutes:.0f} min old — marking failed')
                else:
                    # Leave in processing — webhook will rescue it
                    print(f'⏳  Job {job_id[:8]} still in-flight on fal.ai ({age_minutes:.1f} min old) — keeping processing, waiting for webhook')

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

    # Admin bypass — read from profiles.is_admin (reliable, no env var dependency)
    admin = bool(prof.get('is_admin', False))
    print(f'[admin_check] user={user_id[:8]} is_admin={admin}', flush=True)
    if not admin:
        # Access checks
        if prof.get('status') == 'suspended':
            return jsonify({'error': 'Account suspended. Please update your payment method.'}), 403
        if prof.get('status') in ('cancelled', 'inactive'):
            return jsonify({'error': 'No active subscription. Please start a trial.'}), 403
        # Credit pre-check (precise check happens again after duration is known)
        if prof.get('status') == 'trial' and prof.get('trial_credits_used', 0) >= TRIAL_MAX_CREDITS:
            return jsonify({'error': f'Trial credits exhausted ({TRIAL_MAX_CREDITS} credits). Billing starts on Day 7.'}), 403
        if prof.get('credits_used_this_month', 0) >= prof.get('credits_limit', 10):
            return jsonify({'error': 'Monthly credits exhausted. Upgrade your plan for more.'}), 403

    # Files
    photo = request.files.get('photo')
    audio = request.files.get('audio')
    if not photo or not audio:
        return jsonify({'error': 'photo and audio files are required'}), 400

    style        = (request.form.get('style', 'cinematic') or 'cinematic').lower()
    aspect_ratio = request.form.get('aspect_ratio', '9:16') or '9:16'
    enable_lipsync = request.form.get('enable_lipsync', 'off').lower() in ('on', '1', 'true', 'yes')
    custom_prompt  = (request.form.get('custom_prompt', '') or '').strip()[:900]

    # ── Duration / clip count ────────────────────────────────────────────────
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

    endpoint       = endpoint_for_duration(target_secs)
    n_clips        = n_clips_for_duration(target_secs) if target_secs > 10 else 1
    est_cost       = estimate_cost(target_secs, endpoint)
    credits_needed = _credits_for_duration(target_secs)

    # Precise credit check now that we know the actual duration (admin bypasses)
    if not admin:
        if prof.get('status') == 'trial':
            if prof.get('trial_credits_used', 0) + credits_needed > TRIAL_MAX_CREDITS:
                return jsonify({'error': f'This reel requires {credits_needed} credits but your trial only has {TRIAL_MAX_CREDITS - prof.get("trial_credits_used", 0)} left.'}), 403
        if prof.get('credits_used_this_month', 0) + credits_needed > prof.get('credits_limit', 10):
            remaining = prof.get('credits_limit', 10) - prof.get('credits_used_this_month', 0)
            return jsonify({'error': f'This reel requires {credits_needed} credits but you only have {remaining} left this month.'}), 403

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
        'id':               job_id,
        'user_id':          user_id,
        'status':           'queued',
        'style':            style,
        'aspect_ratio':     aspect_ratio,
        'estimated_cost':   est_cost,
        'enable_lipsync':   enable_lipsync,       # stored in DB — cross-instance safe
        'target_secs_requested': int(target_secs), # stored in DB — cross-instance safe
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
                  est_cost, tmp_dir, target_secs, ext_audio, enable_lipsync, custom_prompt),
            kwargs={'is_admin': admin},
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_run_pipeline,
            args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  est_cost, tmp_dir, target_secs, n_clips, enable_lipsync, custom_prompt),
            kwargs={'is_admin': admin},
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
                        aspect_ratio, est_cost, tmp_dir, target_secs, ext_audio,
                        enable_lipsync=False, custom_prompt='', is_admin=False):
    import time as _time
    _t0 = _time.time()
    global _global_active
    _jid = job_id[:8]

    # Defensive init — any failure here must still mark the job failed
    try:
        sb = _sb_service()
    except Exception as _e:
        print(f'[pregen/{_jid}] FATAL: _sb_service() failed: {_e}', flush=True)
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    def _log(msg):
        print(f'[pregen/{_jid}] {msg} ({_time.time()-_t0:.1f}s)', flush=True)

    try:
        # 1 — Audio analysis
        _log('audio analysis START')
        update('analyzing')
        analysis = analyze_audio(audio_path)
        gc.collect()
        _log(f'audio analysis DONE bpm={analysis.get("bpm",0):.0f}')

        # 2 — Upload audio to Supabase Storage early (needed for transcription URL)
        audio_key = f'jobs/{job_id}/audio.{ext_audio}'
        _log(f'supabase audio upload START key={audio_key}')
        try:
            with open(audio_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    audio_key, fh,
                    file_options={'content-type': f'audio/{ext_audio}'},
                )
            signed_audio = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 3600)
            audio_signed_url = signed_audio.get('signedURL') or signed_audio.get('signedUrl') or ''
            _log('supabase audio upload DONE')
        except Exception as exc:
            raise RuntimeError(f'Audio upload to Supabase failed: {exc}') from exc

        # 3 — Transcribe audio → extract lyrics (fal-ai/whisper, graceful degradation)
        lyrics: str | None = None
        if not custom_prompt and audio_signed_url:
            _log('whisper transcription START')
            lyrics = transcribe_audio_fal(audio_signed_url)
            if lyrics:
                _log(f'whisper transcription DONE ({len(lyrics)} chars)')
            else:
                _log('whisper: no lyrics detected (instrumental) — melody-based prompt')

        # 4 — Scene prompt: custom (user) or auto (Claude Vision + lyrics)
        update('generating', bpm=analysis['bpm'])
        if custom_prompt:
            prompt = custom_prompt
            _log(f'using custom prompt len={len(prompt)}')
        else:
            _log('claude scene prompt START')
            prompt = generate_scene_prompt(analysis, style, photo_path=photo_path,
                                           lyrics=lyrics)
            _log(f'claude scene prompt DONE len={len(prompt)}')

        # 5 — Upload photo to Supabase Storage → get signed URL for fal.ai
        # (Avoids fal_client.upload_file() which has no timeout and hangs on slow networks)
        ext_photo = (photo_path.rsplit('.', 1)[-1].lower()) or 'jpg'
        photo_key = f'jobs/{job_id}/source.{ext_photo}'
        _log(f'supabase photo upload START key={photo_key}')
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    photo_key, fh.read(),
                    file_options={'content-type': f'image/{ext_photo}'},
                )
            signed_photo = sb.storage.from_('reel-uploads').create_signed_url(photo_key, 3600)
            photo_url = signed_photo.get('signedURL') or signed_photo.get('signedUrl') or ''
            if not photo_url:
                raise RuntimeError(f'Could not get signed URL for photo: {signed_photo}')
            _log(f'supabase photo upload DONE url={photo_url[:80]}')
        except Exception as exc:
            raise RuntimeError(f'Photo upload to Supabase failed: {exc}') from exc

        # 6 — Submit to fal.ai WITH webhook URL
        clip_len    = min(target_secs, 10)
        webhook_url = f'{APP_BASE_URL}/video/webhook/fal'
        _log(f'fal submit START dur={clip_len} webhook={webhook_url}')

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
        _log(f'fal submit DONE req_id={fal["request_id"]}')

        # CRITICAL ORDER: persist fal_request_id to DB FIRST.
        # If the server crashes after submit_reel() but before this update,
        # the req_id would be lost and startup_recovery could never find it.
        # Writing to DB first ensures the req_id survives any subsequent crash.
        update('processing',
               fal_request_id=fal['request_id'],
               fal_endpoint=fal['endpoint'],
               prompt=prompt)

        # Register req_id → job_id in-memory (fast path for same-instance webhook).
        # enable_lipsync / target_secs / is_admin are read from DB in fal_webhook()
        # so cross-instance webhooks always get the correct values.
        with _webhook_lock:
            _fal_req_to_job[fal['request_id']] = job_id

        # Thread ends here. fal.ai is generating the video on their servers.
        # Execution resumes in _run_post_generation when the webhook fires.

    except Exception as exc:
        # Wrap update() in its own try so a Supabase error here doesn't
        # prevent the finally-block cleanup (slot release).
        try:
            update('failed', error_message=str(exc)[:500])
        except Exception as _ue:
            print(f'[pregen/{_jid}] WARNING: could not mark job failed: {_ue}', flush=True)

    finally:
        # ALWAYS release the in-memory slot, regardless of how we exited.
        # Previously this was only in the except block — if update() threw,
        # the slot was never released and the user was permanently locked out.
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
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

    # Look up job_id from in-memory (fast path: same instance that submitted).
    # All other flags are read from DB — the only cross-instance-safe source.
    with _webhook_lock:
        job_id = _fal_req_to_job.pop(req_id, None)

    if not job_id:
        # Cross-instance or post-restart: look up by fal_request_id in DB
        try:
            sb = _sb_service()
            rows = sb.table('reel_jobs').select('id') \
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
            'id,user_id,status,aspect_ratio,estimated_cost,fal_endpoint,'
            'enable_lipsync,target_secs_requested'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'ok': True, 'note': 'job not found'}), 200

    # Idempotency guard — only skip truly completed jobs.
    # Do NOT skip 'failed' jobs: startup_recovery may have marked the job failed
    # while fal.ai was still generating (server restart mid-job). When fal.ai
    # fires the webhook we must still process it to save the video.
    if job.get('status') == 'completed':
        return jsonify({'ok': True}), 200

    user_id        = job['user_id']
    est_cost       = float(job.get('estimated_cost') or 0)
    ar             = job.get('aspect_ratio', '9:16')
    endpoint       = job.get('fal_endpoint', '')
    # Read from DB — always correct regardless of which Render instance handles the webhook
    enable_lipsync = bool(job.get('enable_lipsync', False))
    target_secs    = int(job.get('target_secs_requested') or 10)

    # is_admin from profiles (needed to decide whether to deduct credits)
    try:
        prof     = sb.table('profiles').select('is_admin').eq('user_id', user_id).single().execute().data
        is_admin = bool((prof or {}).get('is_admin', False))
    except Exception:
        is_admin = False

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

        # Spawn post-generation thread (download + lipsync + FFmpeg + Supabase upload)
        thread = threading.Thread(
            target=_run_post_generation,
            args=(job_id, user_id, video_url, ar, est_cost),
            kwargs={'enable_lipsync': enable_lipsync,
                    'target_secs': target_secs,
                    'is_admin': is_admin},
            daemon=True,
        )
        thread.start()
        return jsonify({'ok': True}), 200

    # Unknown status — return 200 to avoid fal.ai retrying indefinitely
    return jsonify({'ok': True, 'note': f'unrecognised status: {status}'}), 200


# ── Phase 2: Post-generation (single clip, after webhook) ─────────────────────
# Short-lived thread (~60s): download raw video + FFmpeg + upload to Supabase

def _run_post_generation(job_id, user_id, raw_video_url, aspect_ratio, est_cost,
                         enable_lipsync=False, target_secs=10, is_admin=False):
    """
    Phase 2 post-generation (single clip, webhook-triggered).

    Flow:
      1. Get audio signed URL from Supabase (needed for lipsync API and local download)
      2. If lipsync: apply_lipsync(raw_video_url, audio_signed_url) → lipsync'd URL
         Falls back to raw_video_url on lipsync failure (never crashes the pipeline).
      3. Download video (lipsync'd or raw)
      4. Download audio to local temp file
      5. FFmpeg assembly — replaces lipsync audio with the original high-quality track
      6. Upload final reel to Supabase reel-outputs
      7. Mark completed
    """
    global _global_active
    sb   = _sb_service()
    tmp  = tempfile.mkdtemp(prefix='dlr_post_')
    _jid = job_id[:8]

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        ar_slug    = aspect_ratio.replace(':', 'x')
        raw_path   = os.path.join(tmp, 'raw.mp4')
        final_path = os.path.join(tmp, f'reel_{ar_slug}.mp4')

        # 1 — Locate audio in Supabase Storage and get signed URL
        # (Audio was uploaded during _run_pre_generation; we need the URL for lipsync
        #  and we need to download it locally for FFmpeg assembly.)
        try:
            files = sb.storage.from_('reel-uploads').list(f'jobs/{job_id}')
            audio_file = next(
                (f for f in files if f.get('name', '').startswith('audio.')), None
            )
            if not audio_file:
                raise RuntimeError('Audio file not found in Supabase Storage')
            audio_key  = f'jobs/{job_id}/{audio_file["name"]}'
            ext_audio  = audio_file['name'].rsplit('.', 1)[-1]
            audio_path = os.path.join(tmp, f'audio.{ext_audio}')

            signed = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 3600)
            audio_signed_url = signed.get('signedURL') or signed.get('signedUrl') or ''
            if not audio_signed_url:
                raise RuntimeError(f'Could not get signed URL for audio: {signed}')
        except Exception as exc:
            raise RuntimeError(f'Audio retrieval failed: {exc}') from exc

        # 2 — Lipsync (optional): submit raw video + audio → animated-lip video URL
        # fal-ai/kling-video/lipsync/audio-to-video  ~$0.0028/s, ~90s latency per clip
        # Graceful degradation: any failure falls back to raw_video_url silently.
        video_download_url = raw_video_url
        if enable_lipsync:
            try:
                print(f'[postgen/{_jid}] lipsync START', flush=True)
                update('lipsyncing')
                video_download_url = apply_lipsync(raw_video_url, audio_signed_url)
                print(f'[postgen/{_jid}] lipsync DONE url={video_download_url[:60]}', flush=True)
            except Exception as exc:
                print(f'[postgen/{_jid}] lipsync FAILED ({exc}) — using raw video', flush=True)
                video_download_url = raw_video_url

        # 3 — Download video (lipsync'd or raw)
        resp = _requests.get(video_download_url, stream=True, timeout=180)
        resp.raise_for_status()
        with open(raw_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)

        # 4 — Download audio to local temp file (for FFmpeg)
        resp_audio = _requests.get(audio_signed_url, stream=True, timeout=120)
        resp_audio.raise_for_status()
        with open(audio_path, 'wb') as fh:
            for chunk in resp_audio.iter_content(chunk_size=65536):
                fh.write(chunk)

        # 5 — FFmpeg assembly: overlay original high-quality audio on the video
        # (Replaces whatever audio the lipsync model embedded with the source track.)
        gc.collect()
        assemble_reel([raw_path], audio_path, final_path, aspect_ratio)
        gc.collect()

        # 6 — Upload final reel to Supabase (streaming — no fh.read() in RAM)
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

        # 8 — Deduct credits (skipped for admin accounts)
        if not is_admin:
            sb.rpc('deduct_credits', {
                'p_user_id': user_id,
                'p_credits': _credits_for_duration(target_secs),
            }).execute()

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


# ── Multi-clip pipeline (webhook-based, DB-tracked) ───────────────────────────
# fal-ai/kling-video/v2.6/pro returns 405 on ALL polling endpoints.
# Each clip is submitted with a unique per-clip webhook URL.
# Clip results are tracked atomically in Supabase (add_clip_result RPC) so that
# any Render instance can receive the webhook and progress the job correctly.

def _run_pipeline(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  est_cost, tmp_dir, target_secs=10, n_clips=1, enable_lipsync=False,
                  custom_prompt='', is_admin=False):
    """
    Multi-clip Phase 1 (short thread ~60s):
    analysis + transcription + scene prompt + uploads + submit N clips.

    All state needed by Phase 2 (_run_assembly) is stored in Supabase so that
    any Render instance can handle the per-clip webhooks. The local tmp_dir is
    cleaned up here; _run_assembly downloads audio from Supabase Storage.
    """
    global _global_active
    _jid = job_id[:8]
    sb = _sb_service()

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        # 1 — Audio analysis + beat-sync cut durations
        update('analyzing')
        analysis = analyze_audio(audio_path)
        gc.collect()
        clip_durations = beat_cut_durations(
            bpm=analysis['bpm'],
            target_secs=float(target_secs),
            n_clips=n_clips,
            max_clip_sec=float(CLIP_LEN_MULTI),
        )
        clip_len = min(max((int(max(clip_durations)) if clip_durations else CLIP_LEN_MULTI), 5), 10)
        print(f'[pipeline/{_jid}] BPM={analysis["bpm"]:.0f} cuts={clip_durations} clip_len={clip_len}s', flush=True)

        # 2 — Upload audio to Supabase → signed URL → transcribe
        ext_audio        = (audio_path.rsplit('.', 1)[-1].lower()) or 'mp3'
        audio_key        = f'jobs/{job_id}/audio.{ext_audio}'
        audio_signed_url = ''
        try:
            with open(audio_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    audio_key, fh,
                    file_options={'content-type': f'audio/{ext_audio}'},
                )
            sig = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 7200)
            audio_signed_url = sig.get('signedURL') or sig.get('signedUrl') or ''
            print(f'[pipeline/{_jid}] audio uploaded', flush=True)
        except Exception as exc:
            print(f'[pipeline/{_jid}] audio upload FAILED: {exc}', flush=True)

        lyrics: str | None = None
        if not custom_prompt and audio_signed_url:
            print(f'[pipeline/{_jid}] whisper START', flush=True)
            lyrics = transcribe_audio_fal(audio_signed_url)
            print(f'[pipeline/{_jid}] whisper {"DONE " + str(len(lyrics)) + " chars" if lyrics else "instrumental"}', flush=True)

        # 3 — Scene prompt
        update('generating', bpm=analysis['bpm'])
        prompt = custom_prompt or generate_scene_prompt(
            analysis, style, photo_path=photo_path, lyrics=lyrics
        )
        print(f'[pipeline/{_jid}] prompt len={len(prompt)}', flush=True)

        # 4 — Upload photo → signed URL for fal.ai
        ext_photo = (photo_path.rsplit('.', 1)[-1].lower()) or 'jpg'
        photo_key = f'jobs/{job_id}/source.{ext_photo}'
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    photo_key, fh.read(),
                    file_options={'content-type': f'image/{ext_photo}'},
                )
            sig_photo = sb.storage.from_('reel-uploads').create_signed_url(photo_key, 3600)
            photo_url = sig_photo.get('signedURL') or sig_photo.get('signedUrl') or ''
            if not photo_url:
                raise RuntimeError(f'No signed URL for photo: {sig_photo}')
        except Exception as exc:
            raise RuntimeError(f'Photo upload failed: {exc}') from exc

        # 5 — Persist clip-tracking state in DB (cross-instance safe)
        update('processing',
               fal_request_id=f'multi:{n_clips}',
               fal_endpoint=ENDPOINT_TURBO,
               prompt=prompt,
               n_clips_expected=n_clips,
               target_secs_requested=int(target_secs),
               clip_results='{}')   # reset in case of retry

        # 6 — Submit N clips, each with its own webhook URL
        for i in range(n_clips):
            wh = f'{APP_BASE_URL}/video/webhook/fal/multi/{job_id}/{i}/{n_clips}'
            try:
                h = submit_reel(
                    photo_url, prompt,
                    duration=clip_len, aspect_ratio=aspect_ratio,
                    endpoint=ENDPOINT_TURBO, webhook_url=wh,
                )
                print(f'[pipeline/{_jid}] clip {i}/{n_clips} req={h["request_id"]}', flush=True)
            except Exception as exc:
                raise RuntimeError(f'fal submit clip {i} failed: {exc}') from exc

        print(f'[pipeline/{_jid}] all {n_clips} clips submitted — waiting for webhooks', flush=True)

    except Exception as exc:
        try:
            update('failed', error_message=str(exc)[:500])
        except Exception:
            pass
        print(f'[pipeline/{_jid}] FAILED: {exc}', flush=True)

    finally:
        # Always release slots and clean local files.
        # _run_assembly runs on whichever instance receives the last webhook;
        # it downloads audio fresh from Supabase Storage, so the local tmp_dir is safe to remove.
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ── Multi-clip webhook: cross-instance aggregation via Supabase ───────────────

@video_bp.route('/webhook/fal/multi/<job_id>/<int:clip_idx>/<int:n_clips>', methods=['POST'])
def fal_webhook_multi(job_id, clip_idx, n_clips):
    """
    fal.ai fires this for each clip in a multi-clip job (any Render instance).
    Uses the add_clip_result RPC for atomic JSONB update so concurrent webhooks
    on different instances don't race. Spawns _run_assembly when all N clips arrive.
    Always returns 200 — fal.ai retries on non-200.
    """
    data      = request.get_json(silent=True) or {}
    status    = data.get('status', '')
    payload   = data.get('payload') or {}
    video_obj = payload.get('video') or {}
    video_url = video_obj.get('url') or payload.get('video_url') or ''
    sb        = _sb_service()

    # Handle fal.ai error
    if status == 'ERROR' or not video_url:
        err = str(data.get('error', 'fal.ai clip error'))[:300]
        print(f'[webhook_multi/{job_id[:8]}] clip {clip_idx} ERROR: {err}', flush=True)
        try:
            job = sb.table('reel_jobs').select('user_id').eq('id', job_id).single().execute().data
            sb.table('reel_jobs').update({
                'status':        'failed',
                'error_message': f'Clip {clip_idx + 1}/{n_clips} failed: {err}',
            }).eq('id', job_id).execute()
            with _lock:
                _active_user_jobs.pop(job['user_id'], None)
                _global_active = max(0, _global_active - 1)
        except Exception:
            pass
        return jsonify({'ok': True}), 200

    # Atomic JSONB update — works correctly across all Render instances
    try:
        result      = sb.rpc('add_clip_result', {
            'p_job_id':   job_id,
            'p_clip_idx': str(clip_idx),
            'p_clip_url': video_url,
        }).execute()
        clip_results = result.data or {}   # {"0": "url0", "2": "url2", ...}
    except Exception as exc:
        print(f'[webhook_multi/{job_id[:8]}] DB update failed: {exc}', flush=True)
        return jsonify({'ok': True}), 200

    n_done = len(clip_results) if isinstance(clip_results, dict) else 0
    print(f'[webhook_multi/{job_id[:8]}] clip {clip_idx} ok — {n_done}/{n_clips} done', flush=True)

    if n_done < n_clips:
        return jsonify({'ok': True}), 200

    # All N clips collected — fetch job and spawn assembly
    try:
        job = sb.table('reel_jobs').select(
            'user_id,aspect_ratio,estimated_cost,n_clips_expected,target_secs_requested,bpm'
        ).eq('id', job_id).single().execute().data
        prof = sb.table('profiles').select('is_admin').eq('user_id', job['user_id']).single().execute().data
    except Exception as exc:
        print(f'[webhook_multi/{job_id[:8]}] job fetch failed: {exc}', flush=True)
        return jsonify({'ok': True}), 200

    # Order clip URLs by index (0, 1, 2, ...)
    ordered_urls = [clip_results.get(str(i)) for i in range(n_clips)]
    if not all(ordered_urls):
        print(f'[webhook_multi/{job_id[:8]}] missing clip URL in: {list(clip_results.keys())}', flush=True)
        return jsonify({'ok': True}), 200

    real_target = int(job.get('target_secs_requested') or n_clips * CLIP_LEN_MULTI)
    job_data = {
        'user_id':      job['user_id'],
        'aspect_ratio': job.get('aspect_ratio', '9:16'),
        'est_cost':     float(job.get('estimated_cost') or 0),
        'target_secs':  real_target,
        'bpm':          float(job.get('bpm') or 128),
        'is_admin':     bool((prof or {}).get('is_admin', False)),
        'n_clips':      n_clips,
    }

    threading.Thread(
        target=_run_assembly,
        args=(job_id, ordered_urls, job_data),
        daemon=True,
    ).start()
    return jsonify({'ok': True}), 200


# ── Assembly: download + FFmpeg + upload (any instance) ───────────────────────

def _run_assembly(job_id: str, clip_urls: list, job_data: dict):
    """
    Multi-clip Phase 2: download clips + audio from Supabase, FFmpeg-assemble, upload.
    Runs on whichever instance received the final webhook — instance-agnostic because
    it fetches audio from Supabase Storage rather than a local temp file.
    """
    global _global_active
    _jid         = job_id[:8]
    sb           = _sb_service()
    user_id      = job_data['user_id']
    aspect_ratio = job_data['aspect_ratio']
    est_cost     = job_data['est_cost']
    target_secs  = job_data['target_secs']
    is_admin     = job_data['is_admin']
    bpm          = job_data['bpm']
    n_clips      = job_data['n_clips']
    tmp          = tempfile.mkdtemp(prefix='dlr_asm_')

    clip_durations = beat_cut_durations(
        bpm=bpm,
        target_secs=float(target_secs),
        n_clips=n_clips,
        max_clip_sec=float(CLIP_LEN_MULTI),
    ) if n_clips > 1 else None

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        ar_slug    = aspect_ratio.replace(':', 'x')
        final_path = os.path.join(tmp, f'reel_{ar_slug}.mp4')

        # Download audio from Supabase Storage (instance-agnostic)
        files      = sb.storage.from_('reel-uploads').list(f'jobs/{job_id}')
        audio_file = next((f for f in files if f.get('name', '').startswith('audio.')), None)
        if not audio_file:
            raise RuntimeError('Audio file not found in Supabase Storage')
        audio_key  = f'jobs/{job_id}/{audio_file["name"]}'
        ext_audio  = audio_file['name'].rsplit('.', 1)[-1]
        audio_path = os.path.join(tmp, f'audio.{ext_audio}')
        sig        = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 3600)
        audio_url  = sig.get('signedURL') or sig.get('signedUrl') or ''
        if not audio_url:
            raise RuntimeError(f'Cannot sign audio URL: {sig}')
        resp_a = _requests.get(audio_url, stream=True, timeout=120)
        resp_a.raise_for_status()
        with open(audio_path, 'wb') as fh:
            for chunk in resp_a.iter_content(chunk_size=65536):
                fh.write(chunk)
        print(f'[assembly/{_jid}] audio downloaded', flush=True)

        # Download all N clips
        video_clips = []
        for i, url in enumerate(clip_urls):
            clip_path = os.path.join(tmp, f'clip_{i}.mp4')
            resp = _requests.get(url, stream=True, timeout=180)
            resp.raise_for_status()
            with open(clip_path, 'wb') as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
            video_clips.append(clip_path)
            print(f'[assembly/{_jid}] clip {i} downloaded', flush=True)

        # FFmpeg assemble with beat-sync
        gc.collect()
        assemble_reel(video_clips, audio_path, final_path, aspect_ratio,
                      max_duration=float(target_secs),
                      clip_durations=clip_durations)
        gc.collect()
        print(f'[assembly/{_jid}] FFmpeg done', flush=True)

        # Upload final reel
        output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'
        with open(final_path, 'rb') as fh:
            sb.storage.from_('reel-outputs').upload(
                output_key, fh,
                file_options={'content-type': 'video/mp4'},
            )
        final_url = sb.storage.from_('reel-outputs').get_public_url(output_key)

        _record_spend(est_cost)
        update('completed', output_url=final_url, actual_cost=est_cost)
        print(f'[assembly/{_jid}] COMPLETED', flush=True)

        if not is_admin:
            sb.rpc('deduct_credits', {
                'p_user_id': user_id,
                'p_credits': _credits_for_duration(target_secs),
            }).execute()

    except Exception as exc:
        update('failed', error_message=str(exc)[:500])
        print(f'[assembly/{_jid}] FAILED: {exc}', flush=True)

    finally:
        # Release slot on this instance (harmless if already released by _run_pipeline)
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp, ignore_errors=True)
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
            'plan,status,credits_limit,credits_used_this_month,trial_credits_used'
        ).eq('user_id', user_id).single().execute()
        return jsonify(prof.data)
    except Exception:
        return jsonify({'plan': None, 'status': 'inactive'}), 200
