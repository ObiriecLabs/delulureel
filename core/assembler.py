import os
import ffmpeg
from typing import List, Optional

SCALE_MAP = {
    '9:16': ('1080', '1920'),
    '16:9': ('1920', '1080'),
    '1:1':  ('1080', '1080'),
}

# Reasonable encode settings for social media delivery
_VIDEO_OPTS = dict(vcodec='libx264', crf=20, preset='fast', pix_fmt='yuv420p')
_AUDIO_OPTS  = dict(acodec='aac', audio_bitrate='192k')


def assemble_reel(
    video_clips: List[str],
    audio_path: str,
    output_path: str,
    aspect_ratio: str = '9:16',
    max_duration: Optional[float] = None,
) -> str:
    """
    Concatenate video clips, overlay the original audio track, scale to target
    aspect ratio and encode to output_path. Returns output_path.
    """
    w, h = SCALE_MAP.get(aspect_ratio, ('1080', '1920'))

    if len(video_clips) == 1:
        video_stream = ffmpeg.input(video_clips[0]).video
    else:
        streams = [ffmpeg.input(c).video for c in video_clips]
        video_stream = ffmpeg.concat(*streams, v=1, a=0)

    # Pad to exact canvas without stretching
    scale_filter = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    video_stream = video_stream.filter('scale', w, h, force_original_aspect_ratio='decrease')
    video_stream = video_stream.filter('pad', w, h, '(ow-iw)/2', '(oh-ih)/2', color='black')

    audio_stream = ffmpeg.input(audio_path).audio

    extra = {}
    if max_duration:
        extra['t'] = max_duration

    (
        ffmpeg
        .output(
            video_stream,
            audio_stream,
            output_path,
            **_VIDEO_OPTS,
            **_AUDIO_OPTS,
            shortest=None,
            **extra,
        )
        .overwrite_output()
        .run(quiet=True, capture_stderr=True)
    )

    return output_path


def extract_segment(
    source: str,
    start: float,
    duration: float,
    output_path: str,
) -> str:
    """Cut a segment from source (video or audio) and write to output_path."""
    (
        ffmpeg
        .input(source, ss=start, t=duration)
        .output(output_path, c='copy')
        .overwrite_output()
        .run(quiet=True)
    )
    return output_path
