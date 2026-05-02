import os
import base64
import re
from typing import Dict, Optional
import anthropic

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    return _client


def _encode_image(photo_path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for a given image file."""
    ext = photo_path.rsplit('.', 1)[-1].lower()
    media_map = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif',
    }
    media_type = media_map.get(ext, 'image/jpeg')
    with open(photo_path, 'rb') as f:
        data = base64.standard_b64encode(f.read()).decode('utf-8')
    return data, media_type


_SYSTEM = """You are a professional AI music video director who writes Kling AI image-to-video prompts.

You will receive the actual photo that will be used as the first frame of the video.
Your prompt MUST be visually grounded in what you see: describe the real subject, their appearance,
the setting, the lighting and colors present in the image — then animate that specific scene.

Rules:
- Refer to the subject naturally: "the person", "the subject", "they", "the figure" — NEVER use @Element1 or any @-syntax
- Keep the prompt under 200 words
- Be specific: camera angle, motion, lighting, color grade, mood
- Match energy to BPM: fast BPM = kinetic movement; slow BPM = smooth, cinematic drift
- The visual description must match what is ACTUALLY in the photo, not generic stock imagery
- When song lyrics are provided, let the lyrical theme, imagery and narrative DRIVE the visual concept.
  Translate the emotional meaning of the lyrics into specific camera movements, color grades, and scene atmosphere.
  Do NOT quote or paraphrase the lyrics literally — express their essence visually.
- Output ONLY the prompt text — no preamble, no labels, no quotes"""

_SYSTEM_BLIND = """You are a professional AI music video director who writes Kling AI image-to-video prompts.

Rules:
- Refer to the subject naturally: "the person", "the subject", "they", "the figure" — NEVER use @Element1 or any @-syntax
- Keep the prompt under 180 words
- Be specific: camera angle, motion, lighting, color grade, mood
- Match energy to BPM: fast BPM = kinetic movement; slow BPM = smooth, cinematic drift
- When song lyrics are provided, let the lyrical theme, imagery and narrative DRIVE the visual concept.
  Translate the emotional meaning of the lyrics into specific camera movements, color grades, and scene atmosphere.
  Do NOT quote or paraphrase the lyrics literally — express their essence visually.
- Output ONLY the prompt text — no preamble, no labels, no quotes"""

_FORMAT_HINTS = {
    '9:16': 'vertical 9:16 — TikTok / Instagram Reels',
    '16:9': 'horizontal 16:9 — YouTube / landscape',
    '1:1':  'square 1:1 — Instagram feed',
}

_STYLE_HINTS = {
    'cinematic': 'anamorphic lens, shallow DOF, moody color grade, golden hour or blue hour lighting',
    'neon':      'neon lights, cyberpunk palette, rain-soaked streets, lens flares, synthwave aesthetic',
    'dreamy':    'soft pastel tones, bokeh, ethereal haze, slow-motion, warm film grain',
    'street':    'handheld camera, urban environment, high contrast, raw energy, documentary feel',
    'minimal':   'clean white or dark studio, single spotlight, elegant slow movement, luxury aesthetic',
}


def generate_scene_prompt(
    audio_analysis: Dict,
    style: str = 'cinematic',
    photo_path: Optional[str] = None,
    lyrics: Optional[str] = None,
    aspect_ratio: str = '9:16',
) -> str:
    bpm           = audio_analysis.get('bpm', 120)
    duration      = audio_analysis.get('duration', 30)
    is_fast       = audio_analysis.get('is_fast', False)
    is_very_fast  = audio_analysis.get('is_very_fast', False)
    is_energetic  = audio_analysis.get('is_energetic', False)
    is_bright     = audio_analysis.get('is_bright', False)

    tempo_desc = (
        'explosive fast cuts, rapid motion, kinetic energy' if is_very_fast
        else 'dynamic movement, punchy rhythm' if is_fast
        else 'smooth flowing movement, graceful camera drift'
    )
    energy_desc     = 'intense, high-impact' if is_energetic else 'atmospheric, emotional'
    brightness_desc = 'bright vivid colors' if is_bright else 'rich dark tones'
    style_hint      = _STYLE_HINTS.get(style, _STYLE_HINTS['cinematic'])
    format_desc     = _FORMAT_HINTS.get(aspect_ratio, f'{aspect_ratio} format')

    # Build lyrics block — truncate to ~600 chars to keep prompt concise
    lyrics_block = ''
    if lyrics and lyrics.strip():
        snippet = lyrics.strip()[:600]
        if len(lyrics.strip()) > 600:
            snippet += '…'
        lyrics_block = (
            f"\n\n— SONG LYRICS (use as the primary narrative driver) —\n"
            f"{snippet}\n"
            f"— END LYRICS —\n"
            f"Let the lyrical themes and imagery shape every visual choice in your prompt.\n"
        )

    closing = (
        "Write a Kling AI image-to-video prompt that animates the subject visible in the photo above. "
        "The camera and motion must feel like a music video clip matching the audio energy described."
        if photo_path and os.path.exists(photo_path)
        else
        "Write a Kling AI image-to-video prompt for a music video clip matching the audio energy described above."
    )

    text_prompt = (
        f"BPM: {bpm:.0f} ({tempo_desc})\n"
        f"Duration: {duration:.0f}s\n"
        f"Energy: {energy_desc}\n"
        f"Color palette: {brightness_desc}\n"
        f"Style: {style} — {style_hint}\n"
        f"Format: {format_desc}"
        f"{lyrics_block}\n\n"
        f"{closing}"
    )

    # ── Vision path: Claude sees the actual photo ─────────────────────────────
    if photo_path and os.path.exists(photo_path):
        try:
            img_data, media_type = _encode_image(photo_path)
            message = _get_client().messages.create(
                model='claude-sonnet-4-6',
                max_tokens=320,
                system=_SYSTEM,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': img_data,
                            },
                        },
                        {
                            'type': 'text',
                            'text': text_prompt,
                        },
                    ],
                }],
            )
        except Exception as exc:
            # Vision failed → fallback to text-only (never crash the pipeline)
            print(f'[scene_director] Vision call failed ({exc}), falling back to text-only')
            return _generate_blind(text_prompt)
    else:
        # No photo available → text-only fallback
        return _generate_blind(text_prompt)

    result = message.content[0].text.strip()
    result = re.sub(r'@Element\d+', 'the subject', result, flags=re.IGNORECASE)
    return result


def _generate_blind(text_prompt: str) -> str:
    """Fallback: generate prompt without vision (text-only, original behaviour)."""
    message = _get_client().messages.create(
        model='claude-sonnet-4-6',
        max_tokens=280,
        system=_SYSTEM_BLIND,
        messages=[{'role': 'user', 'content': text_prompt}],
    )
    result = message.content[0].text.strip()
    result = re.sub(r'@Element\d+', 'the subject', result, flags=re.IGNORECASE)
    return result
