import os
import shutil
import ffmpeg
from typing import List, Optional, Sequence

# Honour FFMPEG_PATH env var — lets Render / Fly override the binary location.
# Defaults to 'ffmpeg' (expected to be on PATH in all standard environments).
_FFMPEG_CMD = os.getenv('FFMPEG_PATH', 'ffmpeg')

SCALE_MAP = {
    '9:16': ('1080', '1920'),
    '16:9': ('1920', '1080'),
    '1:1':  ('1080', '1080'),
}

# Reasonable encode settings for social media delivery
_VIDEO_OPTS = dict(vcodec='libx264', crf=20, preset='fast', pix_fmt='yuv420p')
_AUDIO_OPTS  = dict(acodec='aac', audio_bitrate='192k')


def create_loop_variants(
    base_clip: str,
    n_variants: int,
    tmp_dir: str,
) -> List[str]:
    """
    Crea N varianti da un singolo clip base senza ulteriori chiamate GPU.

    Strategia ping-pong per video musicali:
      - Indici pari  → copia forward (stream-copy, istantaneo)
      - Indici dispari → reverse via FFmpeg (bufferizza ~50MB per clip 10s @ 24fps)

    assemble_reel() usa solo la stream video dei clip — l'audio nei file variante
    è irrilevante e non viene incluso per risparmiare spazio e tempo.

    Risparmio RunPod: (n_variants - 1) chiamate GPU evitate.
    """
    variants: List[str] = []
    for i in range(n_variants):
        out = os.path.join(tmp_dir, f'loop_var_{i}.mp4')
        if i % 2 == 0:
            # Forward: copia diretta, nessun re-encode
            shutil.copy(base_clip, out)
        else:
            # Reverse: video invertito (ping-pong), solo stream video
            try:
                (
                    ffmpeg
                    .input(base_clip)
                    .video
                    .filter('reverse')
                    .output(
                        out,
                        vcodec='libx264', crf=20, preset='fast', pix_fmt='yuv420p',
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True, cmd=_FFMPEG_CMD)
                )
            except Exception:
                # Fallback: se reverse fallisce (es. clip troppo lungo), usa forward
                shutil.copy(base_clip, out)
        variants.append(out)
    return variants


def _trim_clips_to_beats(
    clips: List[str],
    durations: Sequence[float],
    tmp_dir: str,
) -> List[str]:
    """
    Phase 2 — trim each Kling clip to its beat-snapped duration.
    Uses stream-copy (no re-encode) for speed. Returns list of trimmed paths.
    """
    trimmed = []
    for i, (clip, dur) in enumerate(zip(clips, durations)):
        out = os.path.join(tmp_dir, f'clip_{i}_beat.mp4')
        try:
            (
                ffmpeg
                .input(clip, t=float(dur))
                .output(out, c='copy')
                .overwrite_output()
                .run(quiet=True, cmd=_FFMPEG_CMD)
            )
            trimmed.append(out)
        except Exception:
            # Fallback: use original untrimmed clip
            trimmed.append(clip)
    return trimmed


def assemble_reel(
    video_clips: List[str],
    audio_path: str,
    output_path: str,
    aspect_ratio: str = '9:16',
    max_duration: Optional[float] = None,
    clip_durations: Optional[Sequence[float]] = None,
) -> str:
    """
    Concatenate video clips, overlay the original audio track, scale to target
    aspect ratio and encode to output_path. Returns output_path.

    Phase 2: if clip_durations is provided (beat-snapped durations per clip),
    each clip is first trimmed to its duration via stream-copy before concat.
    """
    w, h = SCALE_MAP.get(aspect_ratio, ('1080', '1920'))

    # ── Phase 2: beat-sync trim ──────────────────────────────────────────────
    if clip_durations and len(clip_durations) == len(video_clips) and len(video_clips) > 1:
        tmp_dir = os.path.dirname(video_clips[0])
        video_clips = _trim_clips_to_beats(video_clips, clip_durations, tmp_dir)

    if len(video_clips) == 1:
        video_stream = ffmpeg.input(video_clips[0]).video
    else:
        streams = [ffmpeg.input(c).video for c in video_clips]
        video_stream = ffmpeg.concat(*streams, v=1, a=0)

    # Pad to exact canvas without stretching
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
        .run(quiet=True, capture_stderr=True, cmd=_FFMPEG_CMD)
    )

    return output_path
