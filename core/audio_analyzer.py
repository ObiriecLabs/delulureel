import librosa
import numpy as np
from typing import Dict, List


def analyze_audio(file_path: str, max_duration: float = 20.0) -> Dict:
    """
    Analyze BPM, beat positions, energy peaks, and spectral profile.
    Numba-free, memory-optimised for Render Starter (512 MB RAM).
    Uses onset_detect + chroma_stft instead of beat_track + chroma_cqt.
    n_fft=1024 (halves STFT memory vs default 2048).
    """
    # Load audio — soxr_vhq is bundled with librosa, no resampy needed
    y, sr = librosa.load(file_path, mono=True, duration=max_duration,
                         res_type='soxr_vhq', sr=22050)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # BPM via tempo (lightweight, numba-free)
    tempo = librosa.feature.tempo(y=y, sr=sr)
    bpm = round(float(tempo[0]), 1)

    # Beat times via onset detection (no numba — replaces beat_track)
    hop_length = 512
    n_fft      = 1024   # half the default → 2× less STFT memory

    onsets = librosa.onset.onset_detect(
        y=y, sr=sr, hop_length=hop_length,
        units='time', backtrack=False,
    )
    beat_times: List[float] = onsets[:24].tolist()
    del onsets

    # RMS energy
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    frame_times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop_length
    )
    rms_max    = rms.max() if rms.max() > 0 else 1.0
    energy_norm = rms / rms_max
    del rms

    # Energy peaks
    peak_mask  = energy_norm > 0.72
    peak_times: List[float] = frame_times[peak_mask].tolist()[:12]
    del frame_times, peak_mask

    # Spectral centroid (brightness) — small n_fft saves memory
    centroid = float(
        librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=n_fft)[0].mean()
    )

    # Chroma via STFT — n_fft=1024 halves matrix size vs default
    chroma = librosa.feature.chroma_stft(
        y=y, sr=sr, hop_length=hop_length, n_fft=n_fft
    )
    dominant_pitch_class = int(np.argmax(chroma.mean(axis=1)))
    del chroma, y   # free large arrays before returning

    result = {
        'bpm':               bpm,
        'duration':          round(duration, 2),
        'beat_times':        beat_times,
        'peak_times':        peak_times,
        'energy_mean':       float(energy_norm.mean()),
        'energy_profile':    energy_norm[:60].tolist(),
        'spectral_centroid': centroid,
        'dominant_pitch':    dominant_pitch_class,
        'is_fast':           bpm > 118,
        'is_very_fast':      bpm > 145,
        'is_energetic':      float(energy_norm.mean()) > 0.45,
        'is_bright':         centroid > 3000,
    }
    del energy_norm
    return result
