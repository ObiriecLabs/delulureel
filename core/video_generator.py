import os
import time
from typing import Dict, Optional
import fal_client

# Primary: Kling 3.0 Pro — $0.112/sec audio OFF
ENDPOINT_PRO   = 'fal-ai/kling-video/v3/pro/image-to-video'
# Fallback: Kling 2.5 Turbo — $0.070/sec
ENDPOINT_TURBO = 'fal-ai/kling-video/v2.5/turbo/image-to-video'

COST_PER_SEC = {
    ENDPOINT_PRO:   0.112,
    ENDPOINT_TURBO: 0.070,
}

POLL_INTERVAL = 8    # seconds between status checks
MAX_WAIT      = 600  # 10 minutes hard limit


def estimate_cost(duration_seconds: int, endpoint: str = ENDPOINT_PRO) -> float:
    return round(duration_seconds * COST_PER_SEC.get(endpoint, 0.112), 4)


def submit_reel(
    image_url: str,
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = '9:16',
    use_turbo: bool = False,
) -> Dict:
    """
    Submit a Kling generation job to fal.ai.
    Returns {'request_id', 'endpoint', 'estimated_cost'}.
    """
    endpoint = ENDPOINT_TURBO if use_turbo else ENDPOINT_PRO

    handle = fal_client.submit(
        endpoint,
        arguments={
            'prompt':       prompt,
            'image_url':    image_url,
            'duration':     str(duration),
            'aspect_ratio': aspect_ratio,
        },
    )

    return {
        'request_id':     handle.request_id,
        'endpoint':       endpoint,
        'estimated_cost': estimate_cost(duration, endpoint),
    }


def poll_until_done(request_id: str, endpoint: str = ENDPOINT_PRO) -> str:
    """
    Blocks until the fal.ai job is COMPLETED or raises on FAILED / timeout.
    Returns the video URL.
    """
    deadline = time.time() + MAX_WAIT

    while time.time() < deadline:
        status = fal_client.status(endpoint, request_id, with_logs=False)

        if status.status == 'COMPLETED':
            result = fal_client.result(endpoint, request_id)
            video = result.get('video') or {}
            url = video.get('url') or result.get('video_url')
            if not url:
                raise RuntimeError('fal.ai returned no video URL')
            return url

        if status.status == 'FAILED':
            raise RuntimeError(f'fal.ai generation failed: {getattr(status, "error", "unknown")}')

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f'fal.ai job {request_id} did not complete within {MAX_WAIT}s')
