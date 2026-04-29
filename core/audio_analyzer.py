import librosa
import numpy as np
from typing import Dict, List


def analyze_audio(file_path: str, max_duration: float = 60.0) -> Dict:
    """
    Analyze BPM, beat positions, energy peaks, and spectral profile of an audio file.
    Returns a dict consumed by scene_director and video_generator.
    """
    y, sr = librosa.load(file_path, mono=True, duration=max_duration)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # BPM and beat frames
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times: List[float] = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    # RMS energy
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    frame_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)

    rms_max = rms.max() if rms.max() > 0 else 1.0
    energy_norm = rms / rms_max

    # Energy peaks (drops / climaxes)
    peak_mask = energy_norm > 0.72
    peak_times: List[float] = frame_times[peak_mask].tolist()[:12]

    # Spectral centroid (brightness proxy)
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean())

    # Chroma (key feel, major/minor heuristic)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    dominant_pitch_class = int(np.argmax(chroma.mean(axis=1)))

    bpm = round(float(tempo), 1)

    return {
        'bpm':               bpm,
        'duration':          round(duration, 2),
        'beat_times':        beat_times[:24],    # first 24 beats
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
