"""
fal.ai video generation — direct REST API, no fal-client SDK.

fal_client==1.0.0 has a circular import in its __init__.py that causes
  AttributeError: partially initialized module 'fal_client' has no attribute 'submit'
on Python 3.14 when the module is first imported from a background thread.
Using requests directly avoids the SDK entirely.

fal.ai Queue API:
  POST   https://queue.fal.run/{endpoint}[?fal_webhook={url}]   → {"request_id": "..."}
  GET    https://queue.fal.run/{endpoint}/requests/{id}/status  → {"status": "IN_QUEUE"|...}
  GET    https://queue.fal.run/{endpoint}/requests/{id}         → {"video": {"url": "..."}}
"""

import os
import time
import math
import requests as _req
from typing import Dict, List, Optional


# ── Endpoints ─────────────────────────────────────────────────────────────────

# Single-clip: stable quality (Kling 2.6 Pro — v3 Pro has schema bug on elements field)
ENDPOINT_PRO   = 'fal-ai/kling-video/v2.6/pro/image-to-video'
# Multi-clip: same model as PRO — v2.5-turbo endpoint does not exist on fal.ai (405)
ENDPOINT_TURBO = 'fal-ai/kling-video/v2.6/pro/image-to-video'

COST_PER_SEC = {
    ENDPOINT_PRO:   0.112,
    ENDPOINT_TURBO: 0.112,
}

# ── Pricing ───────────────────────────────────────────────────────────────────

MARGIN_MULTIPLIER = float(os.getenv('COST_MARGIN_MULTIPLIER', '2.5'))
USD_TO_EUR        = 0.925   # update as needed

# ── Clip settings ─────────────────────────────────────────────────────────────

CLIP_LEN_MULTI = 10    # seconds per Kling Turbo clip in multi-clip mode
MAX_AUDIO_SEC  = 600   # cap full-track at 10 min

# ── Polling ───────────────────────────────────────────────────────────────────

POLL_INTERVAL   = 8      # seconds between status checks
MAX_WAIT_SINGLE = 600    # 10 min for a single clip
MAX_WAIT_MULTI  = 2700   # 45 min for multi-clip (full track)

FAL_QUEUE_BASE = 'https://queue.fal.run'


# ── Internal helpers ──────────────────────────────────────────────────────────

def _headers() -> Dict:
    key = os.getenv('FAL_KEY', '')
    return {'Authorization': f'Key {key}', 'Content-Type': 'application/json'}


# ── Cost helpers ──────────────────────────────────────────────────────────────

def estimate_cost(duration_seconds: int, endpoint: str = ENDPOINT_PRO) -> float:
    """Raw API cost in USD (for internal tracking / budget cap)."""
    return round(duration_seconds * COST_PER_SEC.get(endpoint, 0.112), 4)


def estimate_user_cost_eur(duration_seconds: int, endpoint: str = ENDPOINT_PRO) -> float:
    """User-facing cost in EUR including margin."""
    raw = duration_seconds * COST_PER_SEC.get(endpoint, 0.112)
    return round(raw * MARGIN_MULTIPLIER * USD_TO_EUR, 2)


def n_clips_for_duration(target_sec: int, clip_len: int = CLIP_LEN_MULTI) -> int:
    """Number of clips needed to cover target_sec seconds."""
    return max(1, math.ceil(target_sec / clip_len))


def endpoint_for_duration(target_sec: int) -> str:
    """Choose model based on target duration."""
    return ENDPOINT_PRO if target_sec <= 10 else ENDPOINT_TURBO


# ── Submission ────────────────────────────────────────────────────────────────

def submit_reel(
    image_url: str,
    prompt: str,
    duration: int = 10,
    aspect_ratio: str = '9:16',
    endpoint: str = ENDPOINT_PRO,
    webhook_url: Optional[str] = None,
) -> Dict:
    """
    Submit a single clip to fal.ai queue.
    Returns {'request_id', 'endpoint', 'estimated_cost'}.
    """
    url = f'{FAL_QUEUE_BASE}/{endpoint}'
    if webhook_url:
        url += f'?fal_webhook={webhook_url}'

    body = {
        'prompt':          prompt,
        'start_image_url': image_url,    # Kling v2.6 Pro: start_image_url
        'duration':        str(duration), # v2.6 Pro expects "5" or "10" string
        'aspect_ratio':    aspect_ratio,
        'generate_audio':  False,        # CRITICAL: disable — default=True tries
                                         # elements[1] (voice), fails with
                                         # 'Invalid reference index 1' when absent
    }

    resp = _req.post(url, json=body, headers=_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()

    req_id = data.get('request_id') or data.get('id') or ''
    if not req_id:
        raise RuntimeError(f'fal.ai did not return request_id: {data}')

    return {
        'request_id':     req_id,
        'endpoint':       endpoint,
        'estimated_cost': estimate_cost(duration, endpoint),
    }


def submit_multi_reel(
    image_url: str,
    prompt: str,
    n_clips: int,
    clip_len: int = CLIP_LEN_MULTI,
    aspect_ratio: str = '9:16',
) -> List[Dict]:
    """
    Submit N Kling Turbo clips in parallel (all enqueued before polling begins).
    Returns list of {'request_id', 'endpoint'}.
    """
    handles = []
    for _ in range(n_clips):
        url  = f'{FAL_QUEUE_BASE}/{ENDPOINT_TURBO}'
        body = {
            'prompt':          prompt,
            'start_image_url': image_url,   # v2.6 Pro: start_image_url
            'duration':        str(clip_len), # v2.6 Pro expects string "5" or "10"
            'aspect_ratio':    aspect_ratio,
            'generate_audio':  False,
        }
        resp = _req.post(url, json=body, headers=_headers(), timeout=60)
        resp.raise_for_status()
        data = resp.json()
        req_id = data.get('request_id') or data.get('id') or ''
        if not req_id:
            raise RuntimeError(f'fal.ai did not return request_id: {data}')
        handles.append({'request_id': req_id, 'endpoint': ENDPOINT_TURBO})
    return handles


# ── Status / result ───────────────────────────────────────────────────────────

def fal_status(endpoint: str, request_id: str) -> Dict:
    """
    Return fal.ai queue status dict for request_id.

    Tries /status suffix first (old-style queue API).
    If that returns 405 (v2.6/pro endpoints), falls back to the request URL
    directly and synthesises a status dict from whatever fal.ai returns.
    """
    status_url = f'{FAL_QUEUE_BASE}/{endpoint}/requests/{request_id}/status'
    resp = _req.get(status_url, headers=_headers(), timeout=30)

    if resp.status_code == 405:
        # v2.6/pro: /status suffix not supported — poll the result URL directly
        result_url = f'{FAL_QUEUE_BASE}/{endpoint}/requests/{request_id}'
        resp = _req.get(result_url, headers=_headers(), timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # If the response already contains a video, the job completed
        if data.get('video') or data.get('video_url'):
            return {'status': 'COMPLETED', '_result': data}
        # Propagate whatever status fal.ai embedded (or assume IN_PROGRESS)
        return {'status': data.get('status', 'IN_PROGRESS')}

    resp.raise_for_status()
    return resp.json()


def fal_result(endpoint: str, request_id: str) -> Dict:
    """Fetch completed result dict from fal.ai queue."""
    url  = f'{FAL_QUEUE_BASE}/{endpoint}/requests/{request_id}'
    resp = _req.get(url, headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


# ── Polling ───────────────────────────────────────────────────────────────────

def poll_until_done(
    request_id: str,
    endpoint: str = ENDPOINT_PRO,
    max_wait: int = MAX_WAIT_SINGLE,
) -> str:
    """
    Blocks until the fal.ai job is COMPLETED or raises on FAILED / timeout.
    Returns the video URL.
    """
    deadline = time.time() + max_wait

    while time.time() < deadline:
        status_data = fal_status(endpoint, request_id)
        status_str  = status_data.get('status', '')

        if status_str == 'COMPLETED':
            # fal_status may have already fetched the result (fallback path)
            result = status_data.get('_result') or fal_result(endpoint, request_id)
            video  = result.get('video') or {}
            url    = video.get('url') or result.get('video_url') or ''
            if not url:
                raise RuntimeError(f'fal.ai returned no video URL: {result}')
            return url

        if status_str == 'FAILED':
            err = status_data.get('error') or status_data.get('logs') or 'unknown'
            raise RuntimeError(f'fal.ai generation failed: {err}')

        # IN_QUEUE or IN_PROGRESS — keep waiting
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f'fal.ai job {request_id} did not complete within {max_wait}s')
