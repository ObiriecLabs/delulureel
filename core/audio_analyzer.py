import librosa
import numpy as np
from typing import Dict, List


def analyze_audio(file_path: str) -> Dict:
    """
    Extract BPM, energy and brightness from the first 10 seconds of the track.
    Optimised for Render Starter (512 MB RAM, slow CPU):
      - sr=11025  → 4× less data than sr=22050 with duration=20
      - no onset_detect, no chroma_stft  (not used by scene_director)
      - hop_length=512, n_fft=512        → minimal STFT matrix
    Typical runtime: 4-8 s on Render Starter.
    """
    # ── Full file duration (fast metadata read, no decode) ────────────────────
    full_duration = 30.0
    try:
        full_duration = float(librosa.get_duration(path=file_path))
    except TypeError:
        try:
            full_duration = float(librosa.get_duration(filename=file_path))
        except Exception:
            pass
    except Exception:
        pass

    # ── Load first 10 s at 11025 Hz (mono) ───────────────────────────────────
    # res_type='kaiser_fast' uses scipy (always available with librosa).
    # 'soxr_vhq' would require the optional soxr package which is NOT in
    # our requirements — causing librosa.load() to crash on Render.
    y, sr = librosa.load(file_path, mono=True, duration=10.0,
                         res_type='kaiser_fast', sr=11025)

    hop_length = 512
    n_fft      = 512

    # BPM (lightweight tempo estimator, numba-free)
    tempo = librosa.feature.tempo(y=y, sr=sr)
    bpm   = round(float(tempo[0]), 1)

    # RMS energy
    rms         = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_max     = rms.max() if rms.max() > 0 else 1.0
    energy_mean = float((rms / rms_max).mean())
    del rms

    # Spectral centroid (brightness) — n_fft=512 at sr=11025 is plenty
    centroid = float(
        librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=n_fft)[0].mean()
    )
    del y

    return {
        'bpm':          bpm,
        'duration':     round(full_duration, 2),
        'is_fast':      bpm > 118,
        'is_very_fast': bpm > 145,
        'is_energetic': energy_mean > 0.45,
        'is_bright':    centroid > 3000,
    }


def beat_cut_durations(
    bpm: float,
    target_secs: float,
    n_clips: int,
    max_clip_sec: float = 10.0,
) -> List[float]:
    """
    Phase 2 — Beat-synced cuts.

    Splits target_secs into n_clips durations, each snapped to the nearest
    4/4 bar boundary. No extra audio loading: derives the bar grid from BPM alone.

    Rules:
    - Each duration ≤ max_clip_sec (Kling clip limit)
    - Each duration ≥ 1 bar (minimum musically meaningful cut)
    - Durations sum to approximately target_secs
    - Clips are generated at 10s by Kling, then trimmed to their beat duration
      in FFmpeg before concatenation

    Example — 30s at 120 BPM (bar = 2s):
        ideal cuts: 10s, 20s → snapped to 10s (5 bars), 20s (10 bars)
        durations: [10.0, 10.0, 10.0]

    Example — 30s at 90 BPM (bar = 2.67s):
        ideal cut: 10s → nearest bar = 8.0s (3 bars) or 10.67s (4 bars, >10 → rejected)
        durations: [8.0, 8.0, 14.0→capped to 10] → assembler handles residual
    """
    if n_clips <= 1:
        return [round(float(min(target_secs, max_clip_sec)), 3)]

    bpm       = max(float(bpm), 40.0)
    beat_sec  = 60.0 / bpm
    bar_sec   = beat_sec * 4   # one bar in 4/4 time
    bar_sec   = max(bar_sec, 0.25)

    def snap_to_bar(gap: float) -> float:
        """Snap a time gap to the nearest whole-bar count ≤ max_clip_sec."""
        n = max(1, round(gap / bar_sec))
        snapped = n * bar_sec
        # Step back one bar at a time until within max_clip_sec
        while snapped > max_clip_sec and n > 1:
            n -= 1
            snapped = n * bar_sec
        return round(snapped, 3)

    prev      = 0.0
    durations = []

    for i in range(1, n_clips):
        ideal_gap = target_secs / n_clips          # ideal duration for this clip
        remaining = target_secs - prev
        clips_left = n_clips - i                   # clips after this one

        # Don't consume so much that remaining clips each get < 1 bar
        max_gap = remaining - clips_left * bar_sec
        max_gap = min(max_gap, max_clip_sec)

        dur = snap_to_bar(ideal_gap)
        dur = min(dur, max(bar_sec, max_gap))      # respect both limits
        dur = max(dur, bar_sec)                    # at least 1 bar

        durations.append(round(dur, 3))
        prev += dur

    # Last clip: remainder, capped at max_clip_sec
    last = round(min(max(target_secs - prev, bar_sec), max_clip_sec), 3)
    durations.append(last)

    return durations
