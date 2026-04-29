import os
from typing import Dict
import anthropic

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    return _client


_SYSTEM = """You are a professional AI music video director who writes Kling 3.0 Pro prompts.

Rules:
- Always reference the subject as @Element1 (Kling Elements = character consistency)
- Keep the prompt under 180 words
- Be specific: camera angle, motion, lighting, color grade, mood
- Match energy to BPM: fast BPM = kinetic movement; slow BPM = smooth, cinematic drift
- Output ONLY the prompt text — no preamble, no labels, no quotes"""

_STYLE_HINTS = {
    'cinematic': 'anamorphic lens, shallow DOF, moody color grade, golden hour or blue hour lighting',
    'neon':      'neon lights, cyberpunk palette, rain-soaked streets, lens flares, synthwave aesthetic',
    'dreamy':    'soft pastel tones, bokeh, ethereal haze, slow-motion, warm film grain',
    'street':    'handheld camera, urban environment, high contrast, raw energy, documentary feel',
    'minimal':   'clean white or dark studio, single spotlight, elegant slow movement, luxury aesthetic',
}


def generate_scene_prompt(audio_analysis: Dict, style: str = 'cinematic') -> str:
    bpm = audio_analysis.get('bpm', 120)
    duration = audio_analysis.get('duration', 30)
    is_fast = audio_analysis.get('is_fast', False)
    is_very_fast = audio_analysis.get('is_very_fast', False)
    is_energetic = audio_analysis.get('is_energetic', False)
    is_bright = audio_analysis.get('is_bright', False)

    tempo_desc = (
        'explosive fast cuts, rapid motion, kinetic energy' if is_very_fast
        else 'dynamic movement, punchy rhythm' if is_fast
        else 'smooth flowing movement, graceful camera drift'
    )
    energy_desc = 'intense, high-impact' if is_energetic else 'atmospheric, emotional'
    brightness_desc = 'bright vivid colors' if is_bright else 'rich dark tones'
    style_hint = _STYLE_HINTS.get(style, _STYLE_HINTS['cinematic'])

    prompt = (
        f"Create a Kling AI music video prompt.\n\n"
        f"BPM: {bpm:.0f} ({tempo_desc})\n"
        f"Duration: {duration:.0f}s\n"
        f"Energy: {energy_desc}\n"
        f"Color: {brightness_desc}\n"
        f"Style: {style} — {style_hint}\n"
        f"Format: vertical 9:16 for TikTok/Reels\n\n"
        f"The subject @Element1 must be the visual focus throughout."
    )

    message = _get_client().messages.create(
        model='claude-sonnet-4-6',
        max_tokens=280,
        system=_SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
    )

    return message.content[0].text.strip()
