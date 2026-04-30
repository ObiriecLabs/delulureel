import librosa
import numpy as np
from typing import Dict


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
