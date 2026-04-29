import librosa
import numpy as np
from typing import Dict, List


def analyze_audio(file_path: str, max_duration: float = 30.0) -> Dict:
    """
    Analyze BPM, beat positions, energy peaks, and spectral profile.
    Numba-free implementation for fast execution on limited hardware.
    Uses onset_detect + chroma_stft instead of beat_track + chroma_cqt.
    """
    # Load audio — kaiser_fast resampler avoids heavy computation
    y, sr = librosa.load(file_path, mono=True, duration=max_duration,
                         res_type='soxr_vhq', sr=22050)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # BPM via tempo (lightweight, numba-free)
    tempo = librosa.feature.tempo(y=y, sr=sr)
    bpm = round(float(tempo[0]), 1)

    # Beat times via onset detection (no numba — replaces beat_track)
    hop_length = 512
    onsets = librosa.onset.onset_detect(
        y=y, sr=sr, hop_length=hop_length,
        units='time', backtrack=False,
    )
    beat_times: List[float] = onsets[:24].tolist()

    # RMS energy
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    frame_times = librosa.frames_to_time(
        np.arange(len(rms)), sr=sr, hop_length=hop_length
    )
    rms_max = rms.max() if rms.max() > 0 else 1.0
    energy_norm = rms / rms_max

    # Energy peaks
    peak_mask = energy_norm > 0.72
    peak_times: List[float] = frame_times[peak_mask].tolist()[:12]

    # Spectral centroid (brightness)
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean())

    # Chroma via STFT (no numba — replaces chroma_cqt)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop_length)
    dominant_pitch_class = int(np.argmax(chroma.mean(axis=1)))

    return {
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
