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
    generate_clip,
    transcribe_audio_fal,
    estimate_cost, endpoint_for_duration, n_clips_for_duration,
    CLIP_LEN_MULTI, MAX_AUDIO_SEC,
)
from core.audio_analyzer import analyze_audio, beat_cut_durations
from core.prompt_builder import generate_scene_prompt
from core.lipsync import apply_lipsync
from core.assembler import assemble_reel, create_loop_variants

from saas.auth.routes import require_auth_api

video_bp = Blueprint('video', __name__)

MAX_CONCURRENT_PER_USER = 1
MAX_CONCURRENT_GLOBAL   = int(os.getenv('MAX_CONCURRENT_GLOBAL',  10))
TRIAL_MAX_CREDITS       = int(os.getenv('TRIAL_MAX_CREDITS',       6))   # 6 crediti ≈ 1 reel 30s o 3 reel 10s
DAILY_BUDGET_CAP_USD    = float(os.getenv('DAILY_BUDGET_CAP_USD', 200))

def _credits_for_duration(target_secs: int) -> int:
    """Calcola i crediti da scalare: 1 credito = 5 secondi di video generato (minimo 1)."""
    return max(1, math.ceil(target_secs / 5))

# In-memory rate-limit state (replace with Redis in production)
_active_user_jobs: dict[str, str] = {}   # user_id → job_id
_global_active    = 0
_daily: dict      = {'date': str(date.today()), 'usd': 0.0}
_lock             = threading.Lock()


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
    with _lock:
        today = str(date.today())
        if _daily['date'] != today:
            _daily['date'] = today
            _daily['usd']  = 0.0
        return _daily['usd'] + cost <= DAILY_BUDGET_CAP_USD


def _record_spend(cost: float):
    with _lock:
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

def _recover_interactive_clips(sb, job_id, clip_submissions, clip_results, n_clips_expected):
    """
    Stub — fal.ai clip recovery rimossa. RunPod è fire-and-forget.
    Il background thread di clip_submit() aggiorna il DB direttamente.
    """
    return 0, False


def _startup_recovery():
    """
    Al boot: segna tutti i job bloccati in stati di elaborazione come failed.
    I job RunPod sono fire-and-forget — non c'è meccanismo di recovery.
    Evita di lasciare slot occupati da job zombie dopo un restart.
    """
    try:
        sb = _sb_service()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        result = sb.table('reel_jobs').select('id').in_(
            'status', ['processing', 'queued', 'analyzing', 'generating', 'lipsyncing']
        ).lt('created_at', cutoff).execute()

        rows = result.data or []
        if not rows:
            return

        failed_ids = [r['id'] for r in rows]
        sb.table('reel_jobs').update({
            'status':        'failed',
            'error_message': 'Server riavviato durante l\'elaborazione. Riprova.',
        }).in_('id', failed_ids).execute()
        print(f'⚠️  Startup: {len(failed_ids)} job orfani marcati come failed', flush=True)

    except Exception as e:
        print(f'⚠️  Startup recovery fallita (non critico): {e}')


# ── Periodic recovery sweep ───────────────────────────────────────────────────

def _do_interactive_recovery():
    """
    Find interactive jobs stuck in 'generating' for >5 minutes and attempt
    clip recovery from fal.ai.  Age out jobs >2 h with no progress.

    Called by _periodic_recovery_sweep() every 10 minutes and exposed as an
    admin endpoint for manual triggering.
    """
    sb = _sb_service()

    cutoff_recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    rows = (
        sb.table('reel_jobs')
        .select('id,clip_submissions,clip_results,n_clips_expected,created_at')
        .eq('status',      'generating')
        .eq('interactive', True)
        .lt('updated_at',  cutoff_recent)
        .execute()
        .data or []
    )

    if not rows:
        return 0, 0   # (n_recovered, n_failed)

    failed_ids  = []
    n_recovered = 0

    for job in rows:
        job_id      = job['id']
        created_str = job.get('created_at', '')
        try:
            created_dt = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            age_h      = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
        except Exception:
            age_h = 0

        if age_h > 2:
            failed_ids.append(job_id)
            print(f'⚠️  [sweep] job={job_id[:8]} >{age_h:.1f}h — marking failed', flush=True)
            continue

        n_rec, _ = _recover_interactive_clips(
            sb, job_id,
            job.get('clip_submissions') or {},
            job.get('clip_results')     or {},
            job.get('n_clips_expected'),
        )
        n_recovered += n_rec

    if failed_ids:
        sb.table('reel_jobs').update({
            'status':        'failed',
            'error_message': 'Generation timed out. Please retry.',
            'updated_at':    datetime.now(timezone.utc).isoformat(),
        }).in_('id', failed_ids).execute()
        print(f'⚠️  [sweep] {len(failed_ids)} stale interactive job(s) marked failed', flush=True)

    if n_recovered:
        print(f'🔄  [sweep] {n_recovered} clip(s) recovered from fal.ai', flush=True)

    return n_recovered, len(failed_ids)


def _periodic_recovery_sweep():
    """
    Daemon thread: every 10 minutes, scan interactive jobs stuck in 'generating'
    and attempt to recover completed clips from fal.ai.

    Started from app_server.py after blueprint registration (30-second initial
    delay so startup_recovery completes first).
    """
    import time as _time
    _time.sleep(30)   # let startup_recovery finish first
    while True:
        try:
            _do_interactive_recovery()
        except Exception as exc:
            print(f'⚠️  [periodic_recovery] sweep error: {exc}', flush=True)
        _time.sleep(600)   # 10 minutes


# ── Generate ──────────────────────────────────────────────────────────────────

@video_bp.route('/generate', methods=['POST'])
@require_auth_api
def generate():
    import time as _time
    _t0 = _time.time()
    global _global_active
    user_id = request.current_user.id
    print(f'[generate] START user={user_id[:8]}')

    # DB-based cross-instance check: prevents the same user from running two jobs
    # simultaneously when the app has multiple Render instances (in-memory dict is
    # per-process and not shared across instances).
    try:
        _db_check = _sb_service().table('reel_jobs').select('id').eq(
            'user_id', user_id
        ).in_('status', ['queued', 'analyzing', 'generating', 'processing', 'lipsyncing']).limit(1).execute()
        if _db_check.data:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
    except Exception as _e:
        print(f'[generate] DB lock check failed (non-fatal): {_e}')

    with _lock:
        if user_id in _active_user_jobs:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
        if _global_active >= MAX_CONCURRENT_GLOBAL:
            return jsonify({'error': 'Service at capacity. Please try again in a few minutes.'}), 429
        # Atomically reserve the slot — closes TOCTOU race between check and acquire.
        # Without this, two concurrent requests from the same user can both pass the
        # check above before either has written to _active_user_jobs.
        _active_user_jobs[user_id] = '__pending__'
        _global_active += 1

    # Slot is now held. Release it in the finally block if we fail to start the thread.
    _thread_started = False
    tmp_dir = None   # set before try so finally can clean it up safely
    try:
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
        if aspect_ratio not in ('9:16', '16:9', '1:1'):
            aspect_ratio = '9:16'
        enable_lipsync = request.form.get('enable_lipsync', 'off').lower() in ('on', '1', 'true', 'yes')
        custom_prompt  = (request.form.get('custom_prompt', '') or '').strip()[:900]

        # ── Duration / clip count ────────────────────────────────────────────────
        video_duration = (request.form.get('video_duration', '10') or '10').strip()

        try:
            audio_dur_sec = max(0.0, float(request.form.get('audio_duration_sec', '0') or '0'))
        except ValueError:
            audio_dur_sec = 0.0

        if video_duration == 'full':
            # round() instead of int() prevents target_secs=0 for sub-1s clips;
            # max(5,...) enforces Kling's minimum supported duration.
            target_secs = max(5, min(round(audio_dur_sec), MAX_AUDIO_SEC)) if audio_dur_sec > 0 else 10
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

        # Replace pending sentinel with the real job_id before handing off to thread
        with _lock:
            _active_user_jobs[user_id] = job_id

        # ── Dispatch ─────────────────────────────────────────────────────────────
        # Single clip → _run_pre_generation (1 RunPod call, blocking)
        # Multi-clip  → _run_pipeline (1 RunPod + N-1 FFmpeg loop variants)
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
        _thread_started = True

        print(f'[generate] DONE returning job_id ({_time.time()-_t0:.1f}s)')
        return jsonify({
            'job_id':       job_id,
            'status':       'queued',
            'target_secs':  target_secs,
            'n_clips':      n_clips,
        })

    finally:
        if not _thread_started:
            with _lock:
                _active_user_jobs.pop(user_id, None)
                _global_active = max(0, _global_active - 1)
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Phase 1: Pre-generation (single clip, RunPod blocking) ───────────────────
# Thread: analisi audio + Claude + upload + generate_clip() BLOCKING (~2-10 min)
# Al termine chiama _run_post_generation() inline — niente webhook.

def _run_pre_generation(job_id, user_id, photo_path, audio_path, style,
                        aspect_ratio, est_cost, tmp_dir, target_secs, ext_audio,
                        enable_lipsync=False, custom_prompt='', is_admin=False):
    """
    Pre-generation (single clip, RunPod blocking):
    analisi audio → prompt Claude → generate_clip() → _run_post_generation() inline.
    Niente webhook — tutto gira nello stesso thread.
    """
    import time as _time
    _t0 = _time.time()
    global _global_active
    _jid = job_id[:8]

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

    # True una volta che _run_post_generation gestirà il rilascio dello slot.
    # Se rimane False, il finally di qui si occupa del cleanup.
    _post_started = False

    try:
        # 1 — Audio analysis
        _log('audio analysis START')
        update('analyzing')
        analysis = analyze_audio(audio_path)
        gc.collect()
        _log(f'audio analysis DONE bpm={analysis.get("bpm",0):.0f}')

        # 2 — Upload audio a Supabase (necessario per post_generation assembly)
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

        # 3 — Transcription (rimossa fal-ai/whisper — restituisce sempre None)
        lyrics: str | None = None
        if not custom_prompt and audio_signed_url:
            _log('transcription skipped (melody-based prompt, Whisper rimosso)')
            lyrics = transcribe_audio_fal(audio_signed_url)  # sempre None

        # 4 — Scene prompt
        update('generating', bpm=analysis['bpm'])
        if custom_prompt:
            prompt = custom_prompt
            _log(f'using custom prompt len={len(prompt)}')
        else:
            _log('claude scene prompt START')
            prompt = generate_scene_prompt(analysis, style, photo_path=photo_path,
                                           lyrics=lyrics, aspect_ratio=aspect_ratio)
            _log(f'claude scene prompt DONE len={len(prompt)}')

        # 5 — Upload photo a Supabase (per audit e futura lipsync)
        ext_photo = (photo_path.rsplit('.', 1)[-1].lower()) or 'jpg'
        photo_key = f'jobs/{job_id}/source.{ext_photo}'
        _log(f'supabase photo upload START key={photo_key}')
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    photo_key, fh.read(),
                    file_options={'content-type': f'image/{ext_photo}'},
                )
            _log('supabase photo upload DONE')
        except Exception as exc:
            raise RuntimeError(f'Photo upload to Supabase failed: {exc}') from exc

        # 6 — Genera clip via RunPod+ComfyUI (BLOCKING ~2-10 min)
        update('processing', prompt=prompt)
        _log(f'generate_clip START ar={aspect_ratio}')
        raw_video_url = generate_clip(
            photo_path, prompt,
            aspect_ratio=aspect_ratio,
        )
        _log(f'generate_clip DONE url={raw_video_url[:60]}')

        # 7 — Post-generation inline (download + FFmpeg + Supabase + mark completed)
        # Da qui il rilascio del slot è responsabilità di _run_post_generation.
        _post_started = True
        _run_post_generation(
            job_id, user_id, raw_video_url, aspect_ratio, est_cost,
            enable_lipsync=enable_lipsync,
            target_secs=target_secs,
            is_admin=is_admin,
        )

    except Exception as exc:
        try:
            update('failed', error_message=str(exc)[:500])
        except Exception as _ue:
            print(f'[pregen/{_jid}] WARNING: could not mark job failed: {_ue}', flush=True)

    finally:
        # Rilascia slot solo se _run_post_generation NON è stata chiamata.
        # (_run_post_generation ha il proprio finally che gestisce il rilascio.)
        if not _post_started:
            with _lock:
                _active_user_jobs.pop(user_id, None)
                _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ── Webhook: fal.ai notifies us when generation is complete ───────────────────

@video_bp.route('/webhook/fal', methods=['POST'])
def fal_webhook():
    """Stub — fal.ai rimosso. RunPod usa polling, niente webhook entrante."""
    return jsonify({'ok': True, 'note': 'fal.ai removed — RunPod polling only'}), 200


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
    Multi-clip pipeline (RunPod × 1 + FFmpeg × N-1):

    1. Genera 1 solo clip via RunPod (~2-10 min, costo GPU una tantum)
    2. Crea N varianti ping-pong dal clip base via FFmpeg (0 costo GPU)
    3. Upload varianti su Supabase → _run_assembly scarica e assembla beat-sync

    Risparmio rispetto all'approccio N×RunPod:
      30s (3 clip) → 3× meno costo
      60s (6 clip) → 6× meno costo
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
        print(f'[pipeline/{_jid}] BPM={analysis["bpm"]:.0f} cuts={clip_durations} n_clips={n_clips}', flush=True)

        # 2 — Upload audio a Supabase (necessario per assembly)
        ext_audio = (audio_path.rsplit('.', 1)[-1].lower()) or 'mp3'
        audio_key = f'jobs/{job_id}/audio.{ext_audio}'
        try:
            with open(audio_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    audio_key, fh,
                    file_options={'content-type': f'audio/{ext_audio}'},
                )
            print(f'[pipeline/{_jid}] audio uploaded', flush=True)
        except Exception as exc:
            print(f'[pipeline/{_jid}] audio upload FAILED: {exc}', flush=True)

        # 3 — Un solo prompt Claude (basta per il clip base RunPod)
        update('generating', bpm=analysis['bpm'])
        if custom_prompt:
            base_prompt = custom_prompt
        else:
            base_prompt = generate_scene_prompt(
                analysis, style, photo_path=photo_path, lyrics=None,
                aspect_ratio=aspect_ratio, clip_index=0, n_clips=n_clips,
            )
        print(f'[pipeline/{_jid}] prompt generated len={len(base_prompt)}', flush=True)

        # 4 — Upload photo (per audit e futura lipsync)
        ext_photo = (photo_path.rsplit('.', 1)[-1].lower()) or 'jpg'
        photo_key = f'jobs/{job_id}/source.{ext_photo}'
        try:
            with open(photo_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    photo_key, fh.read(),
                    file_options={'content-type': f'image/{ext_photo}'},
                )
        except Exception as exc:
            raise RuntimeError(f'Photo upload failed: {exc}') from exc

        # 5 — Inizializza tracking in DB
        update('processing',
               fal_request_id=f'runpod:ffmpeg:{n_clips}',
               prompt=base_prompt,
               n_clips_expected=n_clips,
               target_secs_requested=int(target_secs),
               clip_results={})

        # 6 — 1 sola chiamata RunPod (invece di N)
        print(f'[pipeline/{_jid}] RunPod clip START (1 clip, {n_clips-1} via FFmpeg)', flush=True)
        base_url = generate_clip(photo_path, base_prompt, aspect_ratio=aspect_ratio)
        print(f'[pipeline/{_jid}] RunPod DONE url={base_url[:60]}', flush=True)

        # 7 — Download clip base in tmp locale
        base_clip_path = os.path.join(tmp_dir, 'base_clip.mp4')
        resp_b = _requests.get(base_url, stream=True, timeout=180)
        resp_b.raise_for_status()
        with open(base_clip_path, 'wb') as fh:
            for chunk in resp_b.iter_content(chunk_size=65536):
                fh.write(chunk)
        print(f'[pipeline/{_jid}] base clip downloaded', flush=True)

        # 8 — Crea N varianti ping-pong via FFmpeg (gratuito, nessuna GPU)
        loop_vars = create_loop_variants(base_clip_path, n_clips, tmp_dir)
        print(f'[pipeline/{_jid}] {len(loop_vars)} FFmpeg variants created', flush=True)

        # 9 — Upload varianti su Supabase → signed URLs per _run_assembly
        clip_urls: list = []
        for i, var_path in enumerate(loop_vars):
            var_key = f'jobs/{job_id}/var_{i}.mp4'
            with open(var_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    var_key, fh,
                    file_options={'content-type': 'video/mp4'},
                )
            sig = sb.storage.from_('reel-uploads').create_signed_url(var_key, 3600)
            var_url = sig.get('signedURL') or sig.get('signedUrl') or ''
            if not var_url:
                raise RuntimeError(f'Cannot get signed URL for variant {i}: {sig}')
            clip_urls.append(var_url)
            print(f'[pipeline/{_jid}] variant {i} uploaded', flush=True)

        # 10 — Assembly inline (download varianti + FFmpeg beat-sync + Supabase + mark completed)
        job_data = {
            'user_id':        user_id,
            'aspect_ratio':   aspect_ratio,
            'est_cost':       float(est_cost),
            'target_secs':    int(target_secs),
            'bpm':            float(analysis['bpm']),
            'is_admin':       is_admin,
            'n_clips':        n_clips,
            'enable_lipsync': enable_lipsync,
        }
        _run_assembly(job_id, clip_urls, job_data)

    except Exception as exc:
        try:
            update('failed', error_message=str(exc)[:500])
        except Exception:
            pass
        print(f'[pipeline/{_jid}] FAILED: {exc}', flush=True)

    finally:
        # Slot sempre rilasciato qui — assembly è inline, non via webhook.
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
    """Stub — fal.ai rimosso. RunPod usa polling, niente webhook entrante."""
    return jsonify({'ok': True, 'note': 'fal.ai removed — RunPod polling only'}), 200


# ── Assembly: download + FFmpeg + upload (any instance) ───────────────────────

def _run_assembly(job_id: str, clip_urls: list, job_data: dict):
    """
    Multi-clip Phase 2: download clips + audio from Supabase, FFmpeg-assemble, upload.
    Runs on whichever instance received the final webhook — instance-agnostic because
    it fetches audio from Supabase Storage rather than a local temp file.
    """
    _jid           = job_id[:8]
    sb             = _sb_service()
    user_id        = job_data['user_id']
    aspect_ratio   = job_data['aspect_ratio']
    est_cost       = job_data['est_cost']
    target_secs    = job_data['target_secs']
    is_admin       = job_data['is_admin']
    bpm            = job_data['bpm']
    n_clips        = job_data['n_clips']
    enable_lipsync = job_data.get('enable_lipsync', False)
    tmp            = tempfile.mkdtemp(prefix='dlr_asm_')

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
        # TTL 7200s — must survive the full assembly + optional lipsync window
        sig        = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 7200)
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

        # Lipsync on the assembled video (applied post-FFmpeg so sync covers the full track)
        if enable_lipsync:
            update('lipsyncing')
            print(f'[assembly/{_jid}] lipsync START', flush=True)
            # Upload assembled video to a temp path so fal.ai can fetch it via URL
            tmp_video_key = f'jobs/{job_id}/assembled_tmp.mp4'
            with open(final_path, 'rb') as fh:
                sb.storage.from_('reel-uploads').upload(
                    tmp_video_key, fh,
                    file_options={'content-type': 'video/mp4'},
                )
            sig_v = sb.storage.from_('reel-uploads').create_signed_url(tmp_video_key, 3600)
            assembled_url = sig_v.get('signedURL') or sig_v.get('signedUrl') or ''
            if not assembled_url:
                raise RuntimeError(f'Cannot sign assembled video URL: {sig_v}')
            lipsync_url = apply_lipsync(assembled_url, audio_url)
            resp_ls = _requests.get(lipsync_url, stream=True, timeout=300)
            resp_ls.raise_for_status()
            with open(final_path, 'wb') as fh:
                for chunk in resp_ls.iter_content(chunk_size=65536):
                    fh.write(chunk)
            print(f'[assembly/{_jid}] lipsync DONE', flush=True)

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
        # Do NOT release the in-memory slot — _run_pipeline already released it
        # in its own finally block (multi-clip Phase 1 always releases after submission).
        # Releasing here would double-decrement _global_active if another job is active.
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


# ── Interactive flow: generate prompts only (Step 1) ─────────────────────────

@video_bp.route('/generate/prompts', methods=['POST'])
@require_auth_api
def generate_prompts():
    """
    Interactive flow Step 1: upload files, analyze audio, generate Claude prompts.
    Returns job_id immediately; background thread updates prompts/photo_url in DB.
    Frontend polls GET /video/prompts/<job_id> until status='prompt_ready'.
    """
    import time as _time
    _t0 = _time.time()
    global _global_active
    user_id = request.current_user.id

    try:
        _db_check = _sb_service().table('reel_jobs').select('id').eq(
            'user_id', user_id
        ).in_('status', ['queued', 'analyzing', 'prompt_ready', 'generating', 'processing', 'lipsyncing']).limit(1).execute()
        if _db_check.data:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
    except Exception as _e:
        print(f'[gen_prompts] DB lock check failed: {_e}')

    with _lock:
        if user_id in _active_user_jobs:
            return jsonify({'error': 'You already have a generation in progress.'}), 429
        if _global_active >= MAX_CONCURRENT_GLOBAL:
            return jsonify({'error': 'Service at capacity. Please try again in a few minutes.'}), 429
        _active_user_jobs[user_id] = '__pending__'
        _global_active += 1

    _thread_started = False
    tmp_dir = None
    try:
        sb = _sb_service()
        try:
            prof = sb.table('profiles').select('*').eq('user_id', user_id).single().execute().data
        except Exception:
            return jsonify({'error': 'Profile not found.'}), 404

        admin = bool(prof.get('is_admin', False))
        if not admin:
            if prof.get('status') == 'suspended':
                return jsonify({'error': 'Account suspended.'}), 403
            if prof.get('status') in ('cancelled', 'inactive'):
                return jsonify({'error': 'No active subscription.'}), 403
            if prof.get('status') == 'trial' and prof.get('trial_credits_used', 0) >= TRIAL_MAX_CREDITS:
                return jsonify({'error': f'Trial credits exhausted ({TRIAL_MAX_CREDITS} credits).'}), 403
            if prof.get('credits_used_this_month', 0) >= prof.get('credits_limit', 10):
                return jsonify({'error': 'Monthly credits exhausted.'}), 403

        photo = request.files.get('photo')
        audio = request.files.get('audio')
        if not photo or not audio:
            return jsonify({'error': 'photo and audio files are required'}), 400

        style        = (request.form.get('style', 'cinematic') or 'cinematic').lower()
        aspect_ratio = request.form.get('aspect_ratio', '9:16') or '9:16'
        if aspect_ratio not in ('9:16', '16:9', '1:1'):
            aspect_ratio = '9:16'
        enable_lipsync = request.form.get('enable_lipsync', 'off').lower() in ('on', '1', 'true', 'yes')
        custom_prompt  = (request.form.get('custom_prompt', '') or '').strip()[:900]

        video_duration = (request.form.get('video_duration', '10') or '10').strip()
        try:
            audio_dur_sec = max(0.0, float(request.form.get('audio_duration_sec', '0') or '0'))
        except ValueError:
            audio_dur_sec = 0.0

        if video_duration == 'full':
            target_secs = max(5, min(round(audio_dur_sec), MAX_AUDIO_SEC)) if audio_dur_sec > 0 else 10
        elif video_duration in ('5', '10', '30'):
            target_secs = int(video_duration)
        else:
            target_secs = 10

        n_clips        = n_clips_for_duration(target_secs) if target_secs > 10 else 1
        est_cost       = estimate_cost(target_secs, endpoint_for_duration(target_secs))
        credits_needed = _credits_for_duration(target_secs)

        if not admin:
            if prof.get('status') == 'trial':
                if prof.get('trial_credits_used', 0) + credits_needed > TRIAL_MAX_CREDITS:
                    return jsonify({'error': f'This reel requires {credits_needed} credits but your trial only has {TRIAL_MAX_CREDITS - prof.get("trial_credits_used", 0)} left.'}), 403
            if prof.get('credits_used_this_month', 0) + credits_needed > prof.get('credits_limit', 10):
                remaining = prof.get('credits_limit', 10) - prof.get('credits_used_this_month', 0)
                return jsonify({'error': f'This reel requires {credits_needed} credits but you only have {remaining} left.'}), 403

        if not _budget_ok(est_cost):
            return jsonify({'error': 'Service temporarily unavailable (daily budget reached).'}), 503

        tmp_dir    = tempfile.mkdtemp(prefix='dlr_ipr_')
        ext_photo  = (photo.filename or 'photo.jpg').rsplit('.', 1)[-1].lower() or 'jpg'
        ext_audio  = (audio.filename or 'audio.mp3').rsplit('.', 1)[-1].lower() or 'mp3'
        photo_path = os.path.join(tmp_dir, f'photo.{ext_photo}')
        audio_path = os.path.join(tmp_dir, f'audio.{ext_audio}')
        photo.save(photo_path)
        audio.save(audio_path)

        job_id = str(uuid.uuid4())
        sb.table('reel_jobs').insert({
            'id':                    job_id,
            'user_id':               user_id,
            'status':                'analyzing',
            'style':                 style,
            'aspect_ratio':          aspect_ratio,
            'estimated_cost':        est_cost,
            'enable_lipsync':        enable_lipsync,
            'target_secs_requested': int(target_secs),
            'n_clips_expected':      n_clips,
            'interactive':           True,
            'clip_results':          {},
            'clip_submissions':      {},
        }).execute()

        with _lock:
            _active_user_jobs[user_id] = job_id

        thread = threading.Thread(
            target=_run_prompt_generation,
            args=(job_id, user_id, photo_path, audio_path, style, aspect_ratio,
                  tmp_dir, target_secs, n_clips, ext_audio, custom_prompt),
            kwargs={'is_admin': admin},
            daemon=True,
        )
        thread.start()
        _thread_started = True

        return jsonify({'job_id': job_id})

    finally:
        if not _thread_started:
            with _lock:
                _active_user_jobs.pop(user_id, None)
                _global_active = max(0, _global_active - 1)
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_prompt_generation(job_id, user_id, photo_path, audio_path, style,
                           aspect_ratio, tmp_dir, target_secs, n_clips,
                           ext_audio, custom_prompt='', is_admin=False):
    """
    Interactive flow Phase 1: analyze audio, whisper, generate N Claude prompts.
    Uploads audio + photo to Supabase. Saves prompts + photo_url in DB.
    Releases the global slot immediately — user controls clip submission.
    """
    global _global_active
    _jid = job_id[:8]
    sb   = _sb_service()

    def update(status, **kw):
        sb.table('reel_jobs').update({'status': status, **kw}).eq('id', job_id).execute()

    try:
        # 1 — Audio analysis
        analysis = analyze_audio(audio_path)
        gc.collect()
        print(f'[promptgen/{_jid}] BPM={analysis["bpm"]:.0f}', flush=True)

        # 2 — Upload audio → signed URL for Whisper
        audio_key = f'jobs/{job_id}/audio.{ext_audio}'
        with open(audio_path, 'rb') as fh:
            sb.storage.from_('reel-uploads').upload(
                audio_key, fh,
                file_options={'content-type': f'audio/{ext_audio}'},
            )
        sig_a = sb.storage.from_('reel-uploads').create_signed_url(audio_key, 7200)
        audio_signed_url = sig_a.get('signedURL') or sig_a.get('signedUrl') or ''

        # 3 — Whisper transcription (graceful degradation)
        lyrics = None
        if not custom_prompt and audio_signed_url:
            print(f'[promptgen/{_jid}] whisper START', flush=True)
            lyrics = transcribe_audio_fal(audio_signed_url)
            print(f'[promptgen/{_jid}] whisper {"DONE " + str(len(lyrics)) + " chars" if lyrics else "instrumental"}', flush=True)

        # 4 — Upload photo → signed URL (stored as photo_url for clip 0)
        ext_photo = (photo_path.rsplit('.', 1)[-1].lower()) or 'jpg'
        photo_key = f'jobs/{job_id}/source.{ext_photo}'
        with open(photo_path, 'rb') as fh:
            sb.storage.from_('reel-uploads').upload(
                photo_key, fh.read(),
                file_options={'content-type': f'image/{ext_photo}'},
            )
        sig_p = sb.storage.from_('reel-uploads').create_signed_url(photo_key, 7200)
        photo_url = sig_p.get('signedURL') or sig_p.get('signedUrl') or ''
        if not photo_url:
            raise RuntimeError(f'Could not sign photo URL: {sig_p}')

        # 5 — Generate N prompts with per-clip shot variation
        print(f'[promptgen/{_jid}] generating {n_clips} prompt(s)', flush=True)
        if custom_prompt:
            prompts = [custom_prompt] * n_clips
        else:
            prompts = [
                generate_scene_prompt(
                    analysis, style, photo_path=photo_path, lyrics=lyrics,
                    aspect_ratio=aspect_ratio, clip_index=i, n_clips=n_clips,
                )
                for i in range(n_clips)
            ]

        # 6 — Save to DB: status=prompt_ready
        update('prompt_ready',
               bpm=analysis['bpm'],
               prompts=prompts,
               photo_url=photo_url)
        print(f'[promptgen/{_jid}] prompt_ready — {n_clips} prompt(s) saved', flush=True)

    except Exception as exc:
        try:
            update('failed', error_message=str(exc)[:500])
        except Exception:
            pass
        print(f'[promptgen/{_jid}] FAILED: {exc}', flush=True)

    finally:
        # Release slot — user now controls clip submission interactively
        with _lock:
            _active_user_jobs.pop(user_id, None)
            _global_active = max(0, _global_active - 1)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@video_bp.route('/prompts/<job_id>')
@require_auth_api
def get_prompts(job_id):
    """Poll endpoint: returns prompts when _run_prompt_generation completes."""
    user_id = request.current_user.id
    try:
        row = _sb_service().table('reel_jobs').select(
            'status,prompts,n_clips_expected,error_message,photo_url'
        ).eq('id', job_id).eq('user_id', user_id).single().execute().data
    except Exception:
        return jsonify({'error': 'not found'}), 404
    if not row:
        return jsonify({'error': 'not found'}), 404

    st = row.get('status', '')
    if st == 'prompt_ready':
        return jsonify({
            'status':    'ready',
            'prompts':   row.get('prompts') or [],
            'n_clips':   int(row.get('n_clips_expected') or 1),
            'photo_url': row.get('photo_url') or '',
        })
    if st == 'failed':
        return jsonify({'status': 'failed', 'error': row.get('error_message') or 'Generation failed'})
    return jsonify({'status': 'analyzing'})


# ── Interactive flow: per-clip submission and status ──────────────────────────

@video_bp.route('/clip/submit', methods=['POST'])
@require_auth_api
def clip_submit():
    """
    Avvia la generazione di un singolo clip via RunPod (interactive flow).
    Spawna un thread background che chiama generate_clip() blocking,
    poi salva il risultato in DB tramite add_clip_result RPC.
    Il frontend fa polling su /clip/status/<job_id>/<idx>.
    """
    user_id = request.current_user.id
    data    = request.get_json(silent=True) or {}
    job_id     = (data.get('job_id') or '').strip()
    clip_index = int(data.get('clip_idx', data.get('clip_index', 0)))
    prompt     = (data.get('prompt') or '').strip()
    photo_url  = (data.get('photo_url') or '').strip()

    if not job_id or not prompt:
        return jsonify({'error': 'job_id and prompt are required'}), 400

    try:
        row = _sb_service().table('reel_jobs').select(
            'user_id,aspect_ratio,target_secs_requested,n_clips_expected,'
            'clip_results,status,photo_url'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'error': 'job not found'}), 404

    if not row or row['user_id'] != user_id:
        return jsonify({'error': 'not found'}), 404
    if row.get('status') not in ('prompt_ready', 'generating'):
        return jsonify({'error': f'invalid state: {row.get("status")}'}), 400

    if not photo_url:
        photo_url = (row.get('photo_url') or '').strip()
    if not photo_url:
        return jsonify({'error': 'photo_url not available'}), 400

    aspect_ratio = row['aspect_ratio']

    # Segna subito come generating in modo che il frontend veda il cambio stato
    _sb_service().table('reel_jobs').update({'status': 'generating'}).eq('id', job_id).execute()

    # Cattura le variabili per il thread (evita chiusure su variabili mutabili)
    _job_id      = job_id
    _clip_index  = clip_index
    _photo_url   = photo_url
    _prompt      = prompt
    _aspect      = aspect_ratio

    def _gen_thread():
        try:
            url = generate_clip(_photo_url, _prompt, aspect_ratio=_aspect)
            _sb_service().rpc('add_clip_result', {
                'p_job_id':   _job_id,
                'p_clip_idx': str(_clip_index),
                'p_clip_url': url,
            }).execute()
            print(f'[clip_submit] job={_job_id[:8]} clip={_clip_index} DONE url={url[:60]}', flush=True)
        except Exception as exc:
            print(f'[clip_submit] job={_job_id[:8]} clip={_clip_index} FAILED: {exc}', flush=True)
            try:
                _sb_service().table('reel_jobs').update({
                    'status':        'failed',
                    'error_message': f'Clip {_clip_index} generation failed: {str(exc)[:300]}',
                }).eq('id', _job_id).execute()
            except Exception:
                pass

    threading.Thread(target=_gen_thread, daemon=True).start()
    print(f'[clip_submit] job={job_id[:8]} clip={clip_index} RunPod thread spawned', flush=True)
    return jsonify({'ok': True, 'clip_index': clip_index})


@video_bp.route('/clip/status/<job_id>/<int:idx>')
@require_auth_api
def clip_status_interactive(job_id, idx):
    """
    Status check per un singolo clip interattivo.
    Il risultato viene scritto in DB dal thread generate_clip() — leggiamo da lì.
    Nessuna chiamata diretta a RunPod.
    """
    user_id = request.current_user.id
    try:
        row = _sb_service().table('reel_jobs').select(
            'user_id,clip_results,status'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'error': 'not found'}), 404

    if not row or row['user_id'] != user_id:
        return jsonify({'error': 'not found'}), 404

    if row.get('status') == 'failed':
        return jsonify({'status': 'failed'})

    clip_results = row.get('clip_results') or {}
    if isinstance(clip_results, str):
        clip_results = json.loads(clip_results)

    if str(idx) in clip_results:
        return jsonify({'status': 'completed', 'clip_url': clip_results[str(idx)]})

    return jsonify({'status': 'generating'})


@video_bp.route('/clip/last-frame/<job_id>/<int:idx>', methods=['POST'])
@require_auth_api
def clip_last_frame(job_id, idx):
    """Extract the last frame of clip idx via FFmpeg and return a signed URL."""
    import subprocess
    user_id = request.current_user.id
    try:
        row = _sb_service().table('reel_jobs').select(
            'user_id,clip_results'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'error': 'not found'}), 404

    if not row or row['user_id'] != user_id:
        return jsonify({'error': 'not found'}), 404

    clip_results = row.get('clip_results') or {}
    if isinstance(clip_results, str):
        clip_results = json.loads(clip_results)
    clip_url = clip_results.get(str(idx))
    if not clip_url:
        return jsonify({'error': 'clip not ready'}), 400

    tmp = tempfile.mkdtemp(prefix='dlr_frm_')
    try:
        clip_path  = os.path.join(tmp, 'clip.mp4')
        frame_path = os.path.join(tmp, 'last_frame.jpg')

        resp = _requests.get(clip_url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(clip_path, 'wb') as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)

        # Seek to 0.15s before end to grab last frame
        result = subprocess.run(
            ['ffmpeg', '-sseof', '-0.15', '-i', clip_path,
             '-vframes', '1', '-q:v', '2', '-y', frame_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(frame_path):
            # Fallback: use thumbnail filter
            result2 = subprocess.run(
                ['ffmpeg', '-i', clip_path,
                 '-vf', 'thumbnail', '-frames:v', '1', '-q:v', '2', '-y', frame_path],
                capture_output=True, timeout=30,
            )
            if result2.returncode != 0 or not os.path.exists(frame_path):
                return jsonify({'error': 'frame extraction failed'}), 500

        frame_key = f'jobs/{job_id}/frame_{idx}.jpg'
        sb = _sb_service()
        with open(frame_path, 'rb') as fh:
            sb.storage.from_('reel-uploads').upload(
                frame_key, fh,
                file_options={'content-type': 'image/jpeg', 'upsert': 'true'},
            )
        sig = sb.storage.from_('reel-uploads').create_signed_url(frame_key, 7200)
        frame_url = sig.get('signedURL') or sig.get('signedUrl') or ''
        if not frame_url:
            return jsonify({'error': 'could not sign frame URL'}), 500

        print(f'[last_frame] job={job_id[:8]} clip={idx} → frame signed', flush=True)
        return jsonify({'frame_url': frame_url})

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@video_bp.route('/clip/recover/<job_id>', methods=['POST'])
@require_auth_api
def recover_job(job_id):
    """
    Manual clip recovery: re-check fal.ai for all unresolved clips of a stuck
    interactive job and persist any completed results.
    Returns {'recovered': N, 'all_done': bool}.
    """
    user_id = request.current_user.id
    try:
        row = _sb_service().table('reel_jobs').select(
            'user_id,clip_submissions,clip_results,n_clips_expected,status'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'error': 'not found'}), 404

    if not row or row['user_id'] != user_id:
        return jsonify({'error': 'not found'}), 404

    if row.get('status') not in ('generating', 'prompt_ready'):
        return jsonify({'error': f'job not recoverable in state: {row.get("status")}'}), 400

    n_rec, all_done = _recover_interactive_clips(
        _sb_service(), job_id,
        row.get('clip_submissions') or {},
        row.get('clip_results')     or {},
        row.get('n_clips_expected'),
    )
    return jsonify({'recovered': n_rec, 'all_done': all_done})


@video_bp.route('/assemble/<job_id>', methods=['POST'])
@require_auth_api
def assemble_interactive(job_id):
    """
    Interactive flow final step: user confirmed all clips — spawn _run_assembly.
    """
    user_id = request.current_user.id
    try:
        row = _sb_service().table('reel_jobs').select(
            'user_id,aspect_ratio,estimated_cost,target_secs_requested,bpm,'
            'n_clips_expected,clip_results,enable_lipsync'
        ).eq('id', job_id).single().execute().data
    except Exception:
        return jsonify({'error': 'not found'}), 404

    if not row or row['user_id'] != user_id:
        return jsonify({'error': 'not found'}), 404

    n_clips = int(row.get('n_clips_expected') or 1)
    clip_results = row.get('clip_results') or {}
    if isinstance(clip_results, str):
        clip_results = json.loads(clip_results)

    ordered_urls = [clip_results.get(str(i)) for i in range(n_clips)]
    if not all(ordered_urls):
        missing = [i for i in range(n_clips) if not clip_results.get(str(i))]
        return jsonify({'error': f'clips not ready: {missing}'}), 400

    try:
        prof     = _sb_service().table('profiles').select('is_admin').eq('user_id', user_id).single().execute().data
        is_admin = bool((prof or {}).get('is_admin', False))
    except Exception:
        is_admin = False

    job_data = {
        'user_id':        user_id,
        'aspect_ratio':   row.get('aspect_ratio', '9:16'),
        'est_cost':       float(row.get('estimated_cost') or 0),
        'target_secs':    int(row.get('target_secs_requested') or 10),
        'bpm':            float(row.get('bpm') or 128),
        'is_admin':       is_admin,
        'n_clips':        n_clips,
        'enable_lipsync': bool(row.get('enable_lipsync', False)),
    }

    threading.Thread(
        target=_run_assembly,
        args=(job_id, ordered_urls, job_data),
        daemon=True,
    ).start()

    print(f'[assemble_interactive] job={job_id[:8]} spawned _run_assembly ({n_clips} clips)', flush=True)
    return jsonify({'ok': True})


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
        for _ in range(360):  # max ~60 min at 10s interval (full-track jobs take 20-40 min)
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


# ── Public share endpoint (no auth) ──────────────────────────────────────────

@video_bp.route('/public/<job_id>')
def public_job(job_id):
    """Return public metadata for a shared reel — no auth required.
    Only exposes output_url when status=completed (never raw uploads or prompts).
    """
    sb = _sb_service()
    try:
        job = sb.table('reel_jobs').select(
            'status,output_url,style,aspect_ratio,bpm'
        ).eq('id', job_id).single().execute()
    except Exception:
        return jsonify({'error': 'Not found'}), 404

    data = job.data or {}
    resp = {
        'status':       data.get('status'),
        'style':        data.get('style'),
        'aspect_ratio': data.get('aspect_ratio'),
        'bpm':          data.get('bpm'),
    }
    # Only expose the output URL when the job is completed
    if data.get('status') == 'completed':
        resp['output_url'] = data.get('output_url')
    return jsonify(resp)
