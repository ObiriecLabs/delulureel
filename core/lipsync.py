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


def _headers():
    return {
        'Authorization': f'Key {os.getenv("FAL_KEY", "")}',
        'Content-Type':  'application/json',
    }


def submit_lipsync(video_url: str, audio_url: str) -> str:
    """Submit a lipsync job to fal.ai queue. Returns request_id."""
    url  = f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}'
    body = {'video_url': video_url, 'audio_url': audio_url}
    resp = _req.post(url, json=body, headers=_headers(), timeout=60)
    resp.raise_for_status()
    data   = resp.json()
    req_id = data.get('request_id') or data.get('id') or ''
    if not req_id:
        raise RuntimeError(f'Lipsync: no request_id in response: {data}')
    return req_id


def poll_lipsync(request_id: str, max_wait: int = MAX_WAIT) -> str:
    """Block until the lipsync job completes. Returns the lipsync'd video URL."""
    status_url = f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}/requests/{request_id}/status'
    result_url = f'{FAL_QUEUE_BASE}/{LIPSYNC_ENDPOINT}/requests/{request_id}'
    deadline   = time.time() + max_wait

    while time.time() < deadline:
        resp = _req.get(status_url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        st = resp.json().get('status', '')

        if st == 'COMPLETED':
            res  = _req.get(result_url, headers=_headers(), timeout=60)
            res.raise_for_status()
            data = res.json()
            url  = (data.get('video') or {}).get('url') or data.get('video_url') or ''
            if not url:
                raise RuntimeError(f'Lipsync: no video URL in result: {data}')
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
