import os
import time
import math
from typing import Dict, List

import fal_client


# ── Endpoints ─────────────────────────────────────────────────────────────────

# Single-clip: stable quality (Kling 2.6 Pro — v3 Pro has schema bug on elements field)
ENDPOINT_PRO   = 'fal-ai/kling-video/v2.6/pro/image-to-video'
# Multi-clip: cost-optimised (~38% cheaper, Kling 2.5 Turbo Pro)
ENDPOINT_TURBO = 'fal-ai/kling-video/v2.5-turbo/pro/image-to-video'

COST_PER_SEC = {
    ENDPOINT_PRO:   0.112,
    ENDPOINT_TURBO: 0.070,
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
) -> Dict:
    """
    Submit a single clip to fal.ai.
    Returns {'request_id', 'endpoint', 'estimated_cost'}.
    """
    handle = fal_client.submit(
        endpoint,
        arguments={
            'prompt':          prompt,
            'start_image_url': image_url,   # Kling v2.6/v3 Pro use start_image_url
            'duration':        str(duration),  # v2.6 Pro expects "5" or "10" string
            'aspect_ratio':    aspect_ratio,
            'generate_audio':  False,       # CRITICAL: disable default audio gen
                                            # (default=True tries to use elements[1],
                                            # fails with 'Invalid reference index 1'
                                            # when no voice elements are provided)
        },
    )
    return {
        'request_id':     handle.request_id,
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
        h = fal_client.submit(
            ENDPOINT_TURBO,
            arguments={
                'prompt':          prompt,
                'image_url':       image_url,   # Kling 2.5 Turbo uses image_url
                'duration':        clip_len,    # integer: 5 or 10
                'aspect_ratio':    aspect_ratio,
                'generate_audio':  False,       # disable default audio gen (same bug)
            },
        )
        handles.append({
            'request_id': h.request_id,
            'endpoint':   ENDPOINT_TURBO,
        })
    return handles


# ── Polling ───────────────────────────────────────────────────────────────────

def poll_until_done(
    request_id: str,
    endpoint: str = ENDPOINT_PRO,
    max_wait: int = MAX_WAIT_SINGLE,
) -> str:
    """
    Blocks until the fal.ai job is COMPLETED or raises on FAILED / timeout.
    Returns the video URL.

    Compatible with both fal-client API styles:
    - Old (<0.10): status object has a .status string attribute ('COMPLETED', 'FAILED', …)
    - New (>=0.10): status() returns typed objects (Queued, InProgress, Completed)
    """
    deadline = time.time() + max_wait

    while time.time() < deadline:
        status = fal_client.status(endpoint, request_id, with_logs=False)

        # Detect API style from the returned object
        status_type = type(status).__name__          # 'Queued' | 'InProgress' | 'Completed' (new)
        status_str  = getattr(status, 'status', '')  # 'COMPLETED' | 'FAILED' | '' (old)

        is_done   = (status_type == 'Completed') or (status_str == 'COMPLETED')
        is_failed = (status_type not in ('Queued', 'InProgress', 'Completed')) \
                    and status_type not in ('', 'NoneType') \
                    or (status_str == 'FAILED')

        if is_done:
            result = fal_client.result(endpoint, request_id)
            video = result.get('video') or {}
            url = video.get('url') or result.get('video_url')
            if not url:
                raise RuntimeError('fal.ai returned no video URL')
            return url

        if is_failed:
            err = getattr(status, 'error', None) or getattr(status, 'message', status_type)
            raise RuntimeError(f'fal.ai generation failed: {err}')

        # Queued or InProgress — keep waiting
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f'fal.ai job {request_id} did not complete within {max_wait}s')
