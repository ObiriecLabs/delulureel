"""
fal.ai Kling Lipsync — fal-ai/kling-video/lipsync/audio-to-video

Animates lip movement on a video to match an audio track.
Uses the same direct-REST pattern as video_generator.py (no fal-client SDK).

Cost: ~$0.014 per 5-second clip  (~$0.0028/s)
API:  POST https://queue.fal.run/fal-ai/kling-video/lipsync/audio-to-video
      Body: {video_url, audio_url}
"""

import os
import time
import requests as _req

LIPSYNC_ENDPOINT     = 'fal-ai/kling-video/lipsync/audio-to-video'
FAL_QUEUE_BASE       = 'https://queue.fal.run'
LIPSYNC_COST_PER_SEC = 0.0028   # $0.014 / 5 s

POLL_INTERVAL = 8     # seconds between status checks
MAX_WAIT      = 360   # 6-minute ceiling per clip


def _headers_post() -> dict:
    return {
        'Authorization': f'Key {os.getenv("FAL_KEY", "")}',
        'Content-Type':  'application/json',
    }


def _headers_get() -> dict:
    """GET requests must NOT carry Content-Type — causes 405 on some fal.ai endpoints."""
    return {'Authorization': f'Key {os.getenv("FAL_KEY", "")}'}


def _fal_status_lipsync(request_id: str) -> dict:
    """
    Status check with 4-URL cascade — same pattern as video_generator.fal_result().
    fal.ai returns 405 on some endpoint-scoped /requests/ paths (e.g. newer models).
    Falls back to the global queue URL which always works.
    """
    def _parse(data: dict) -> dict:
        if data.get('video') or data.get('video_url'):
            return {'status': 'COMPLETED', '_result': data}
        return {'status': data.get('status', 'IN_PROGRESS')}

    urls = [
        (f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}/requests/{request_id}/status', 30),
        (f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}/requests/{request_id}',        60),
        (f'{FAL_QUEUE_BASE}/requests/{request_id}/status',                    30),
        (f'{FAL_QUEUE_BASE}/requests/{request_id}',                           60),
    ]
    last_err = None
    for url, timeout in urls:
        resp = _req.get(url, headers=_headers_get(), timeout=timeout)
        if resp.status_code == 405:
            last_err = f'405 on {url}'
            continue
        resp.raise_for_status()
        return _parse(resp.json())
    raise RuntimeError(f'Lipsync status unreachable for {request_id}: {last_err}')


def submit_lipsync(video_url: str, audio_url: str) -> str:
    """Submit a lipsync job to fal.ai queue. Returns request_id."""
    url  = f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}'
    body = {'video_url': video_url, 'audio_url': audio_url}
    resp = _req.post(url, json=body, headers=_headers_post(), timeout=60)
    resp.raise_for_status()
    data   = resp.json()
    req_id = data.get('request_id') or data.get('id') or ''
    if not req_id:
        raise RuntimeError(f'Lipsync: no request_id in response: {data}')
    return req_id


def poll_lipsync(request_id: str, max_wait: int = MAX_WAIT) -> str:
    """Block until the lipsync job completes. Returns the lipsync'd video URL."""
    deadline = time.time() + max_wait

    while time.time() < deadline:
        st_data = _fal_status_lipsync(request_id)
        st      = st_data.get('status', '')

        if st == 'COMPLETED':
            result = st_data.get('_result') or {}
            if not result:
                # Fetch explicitly if _parse didn't embed it
                res    = _req.get(
                    f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}/requests/{request_id}',
                    headers=_headers_get(), timeout=60,
                )
                result = res.json()
            url = (result.get('video') or {}).get('url') or result.get('video_url') or ''
            if not url:
                raise RuntimeError(f'Lipsync: no video URL in result: {result}')
            return url

        if st == 'FAILED':
            raise RuntimeError(f'Lipsync job {request_id} failed on fal.ai')

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f'Lipsync job {request_id} did not complete within {max_wait}s')


def apply_lipsync(video_url: str, audio_url: str, max_wait: int = MAX_WAIT) -> str:
    """Submit + poll lipsync. Blocking. Returns lipsync'd video URL."""
    req_id = submit_lipsync(video_url, audio_url)
    return poll_lipsync(req_id, max_wait)


def estimate_lipsync_cost(duration_seconds: float) -> float:
    """Raw API cost in USD for lipsync of a given duration."""
    return round(duration_seconds * LIPSYNC_COST_PER_SEC, 4)
