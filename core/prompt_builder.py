"""
core/prompt_builder.py
======================
Deterministic WAN I2V prompt builder — ZERO external API calls.

Sostituisce core/scene_director.py che chiamava Anthropic Claude per ogni
generazione utente — violazione del constraint architetturale:
"nessuna chiave API di terze parti consumata lato server per conto degli utenti".

Il prompt viene costruito da:
  - Flag audio (BPM, is_fast, is_very_fast, is_energetic, is_bright) da librosa
  - Stile scelto dall'utente (cinematic / neon / dreamy / street / minimal)
  - Aspect ratio (9:16 / 16:9 / 1:1)
  - clip_index e n_clips (cycling shot variation per clip)
  - Lyrics opzionali (primi 80 chars come tone anchor — nessun parse LLM)

photo_path è accettato per compatibilità API ma NON usato: il conditioning
immagine di WAN I2V àncora già il soggetto visivo. Il testo guida
motion, atmosfera e color grade — non la descrizione del soggetto.

Modalità disponibili
--------------------
  'template'  → prompt deterministico completo da questa funzione (default)
  'custom'    → passa direttamente custom_prompt[:500] (prompt utente)

  Modalità 'llm' (LLM node in ComfyUI/Ollama) è gestita upstream dal
  workflow JSON — questa funzione non interviene in quel caso.

API pubblica
------------
  build_prompt(audio_analysis, style, photo_path, lyrics, aspect_ratio,
               clip_index, n_clips, mode, custom_prompt) -> str

  generate_scene_prompt — alias backward-compat per build_prompt
"""

import re
from typing import Dict, List, Optional


# ── Shot variation pool ───────────────────────────────────────────────────────
# 5 tipi distinti che ciclano per clip_index. Per n_clips > 5 il ciclo si ripete
# ma la variazione è sempre sentita perché camera motion e atmosfera cambiano.

_SHOT_TYPES: List[str] = [
    "wide establishing shot — full environment in frame, subject anchored in the scene",
    "medium shot — waist-up framing, gesture and expression fully readable",
    "tight close-up — face or striking detail in sharp focus, maximum emotional proximity",
    "dramatic low-angle — camera looking up, subject monumental against the sky or ceiling",
    "lateral tracking shot — camera moves parallel to subject, cinematic sense of passage",
]

# ── Camera motion banks — 5 energy tier × 3 varianti ─────────────────────────
# Selezione tier: _camera_tier(is_very_fast, is_fast, is_energetic)

_CAMERA_BANKS: Dict[str, List[str]] = {
    'explosive': [
        "rapid staccato push-ins — camera lunges forward on every beat, raw kinetic aggression, "
        "shutter-speed tension",
        "fast whip-pan energy — handheld frenzy contained in frame, momentum never releases",
        "strobe-pulse motion — camera surges and stalls in sync with the BPM, percussive visual rhythm",
    ],
    'kinetic': [
        "dynamic lateral tracking — controlled speed, purposeful, cuts land precisely on the downbeat",
        "push-pull zoom rhythm — lens breathes in and out with the musical phrase, alive with the music",
        "rising jib arc — camera lifts and sweeps forward, tension building across each bar",
    ],
    'flowing': [
        "fluid orbital arc — camera circles the subject at a consistent, elegant pace",
        "parallax drift — foreground elements glide at different speeds, layered cinematic depth",
        "smooth crane-style rise — unhurried lift, environment gradually revealed in the ascent",
    ],
    'brooding': [
        "slow dramatic push-in — camera inches toward subject with heavy intentionality, deliberate dread",
        "moody lateral drift — barely perceptible float, emotion heavier than any motion",
        "locked handheld breathing — minimal movement, the camera alive with micro-tremors only",
    ],
    'meditative': [
        "glacial cinematic drift — near-imperceptible camera movement, pure atmospheric presence",
        "static locked-off frame — all motion comes from subject, light and wind; camera only witnesses",
        "breath-of-life float — camera weightless and suspended, meditative, reverent stillness",
    ],
}


def _camera_tier(is_very_fast: bool, is_fast: bool, is_energetic: bool) -> str:
    if is_very_fast and is_energetic:
        return 'explosive'
    if is_fast and is_energetic:
        return 'kinetic'
    if is_fast and not is_energetic:
        return 'flowing'
    if not is_fast and is_energetic:
        return 'brooding'
    return 'meditative'


# ── Color / lighting signatures — stile × luminosità ─────────────────────────

_COLOR_SIGS: Dict[str, Dict[str, str]] = {
    'cinematic': {
        'bright': (
            "anamorphic lens flares streak across warm golden-hour light, "
            "shallow depth of field, highlight roll-off, teal-and-orange split tone"
        ),
        'dark': (
            "blue-hour grade, inky lifted shadows, deep blacks that hold texture, "
            "anamorphic lens bloom on every practical light source"
        ),
    },
    'neon': {
        'bright': (
            "electric pink and cyan cross-lighting, neon sign color bleed on every surface, "
            "rain-amplified reflections, vivid saturated palette"
        ),
        'dark': (
            "deep violet shadows, isolated neon halos carving the subject from darkness, "
            "moisture in the air softening edges, underexposed midtones"
        ),
    },
    'dreamy': {
        'bright': (
            "soft pastel wash, warm bokeh blooms out of focus, gossamer haze, "
            "overexposed whites with halation bleeding into the frame"
        ),
        'dark': (
            "cool lavender mist, ethereal blue-green tones, dream-like blur corona, "
            "faded blacks with milky shadow detail"
        ),
    },
    'street': {
        'bright': (
            "high-contrast daylight, harsh directional shadows, "
            "mostly desaturated with one punchy accent color, gritty handheld texture"
        ),
        'dark': (
            "high-contrast night, pools of sodium and mercury-vapor light, "
            "long cast shadows, raw documentary grain"
        ),
    },
    'minimal': {
        'bright': (
            "clean overexposed white studio, single hard key light creating a precise shadow, "
            "luxury negative space, zero distraction"
        ),
        'dark': (
            "dark studio void, single tight spotlight pool, "
            "deep surrounding black, sculptural chiaroscuro"
        ),
    },
}


# ── Atmosphere descriptors — stile × energia ─────────────────────────────────

_ATMOSPHERE: Dict[str, Dict[str, str]] = {
    'cinematic': {
        'high': "intense dramatic atmosphere — every frame carries weight and consequence",
        'low':  "quiet cinematic melancholy — slow-burning tension, emotional undercurrent",
    },
    'neon': {
        'high': "cyberpunk urgency — city electricity, adrenaline and neon excess at full tilt",
        'low':  "synthwave nostalgia — lonely neon poetry, rain and unresolved memory",
    },
    'dreamy': {
        'high': "euphoric dreamstate — everything slightly unreal, feelings overrule physics",
        'low':  "bittersweet reverie — tender introspection, the world soft-focused and still",
    },
    'street': {
        'high': "raw street energy — unscripted intensity, documentary truth in motion",
        'low':  "urban poetry — quiet observation, the city breathing around the subject",
    },
    'minimal': {
        'high': "controlled tension — performance under invisible pressure, precise and deliberate",
        'low':  "contemplative silence — subject and void in dialogue, stillness as statement",
    },
}


# ── Format / composition notes ────────────────────────────────────────────────

_FORMAT_NOTES: Dict[str, str] = {
    '9:16':  (
        "vertical 9:16 frame — subject centered along the vertical axis, "
        "environment framing the negative space on either side"
    ),
    '16:9':  (
        "widescreen 16:9 frame — cinematic horizontal composition, "
        "rule-of-thirds placement, depth extending in the horizontal plane"
    ),
    '1:1':   (
        "square 1:1 frame — balanced symmetric composition, "
        "subject and environment in equal visual dialogue"
    ),
}


# ── Tempo language ────────────────────────────────────────────────────────────

def _tempo_language(bpm: float, is_very_fast: bool, is_fast: bool) -> str:
    if is_very_fast:
        return (
            f"{bpm:.0f} BPM explosive tempo — "
            "camera and subject move at the relentless speed of the music"
        )
    if is_fast:
        return (
            f"{bpm:.0f} BPM driving rhythm — "
            "motion is purposeful and locked to the beat, no idle frames"
        )
    if bpm > 90:
        return (
            f"{bpm:.0f} BPM mid-tempo — "
            "fluid movement with deliberate space between each action"
        )
    return (
        f"{bpm:.0f} BPM slow burn — "
        "every second of motion carries weight, nothing wasted"
    )


# ── Lyrics tone anchor (zero LLM — raw sanitized pass-through) ───────────────

def _lyrics_anchor(lyrics: Optional[str]) -> str:
    """Estrae i primi 80 chars dei lyrics come anchor tonale nel prompt.
    Nessuna analisi semantica — il text encoder di WAN legge il tono dai token.
    """
    if not lyrics or not lyrics.strip():
        return ''
    # Collassa whitespace, rimuovi caratteri potenzialmente problematici per il prompt
    cleaned = re.sub(r'[^\w\s\'\-\.,!?]', '', lyrics.strip())
    cleaned = re.sub(r'\s+', ' ', cleaned)
    snippet = cleaned[:80].strip()
    if len(cleaned) > 80:
        snippet += '…'
    if not snippet:
        return ''
    return f'Lyrical tone: "{snippet}" — let this emotional undercurrent shape the visual atmosphere.'


# ── Main builder ──────────────────────────────────────────────────────────────

def build_prompt(
    audio_analysis: Dict,
    style: str = 'cinematic',
    photo_path: Optional[str] = None,   # API compat — non usato (WAN usa image conditioning)
    lyrics: Optional[str] = None,
    aspect_ratio: str = '9:16',
    clip_index: int = 0,
    n_clips: int = 1,
    mode: str = 'template',             # 'template' | 'custom'
    custom_prompt: Optional[str] = None,
) -> str:
    """
    Costruisce un prompt WAN I2V in modo deterministico da audio analysis + stile.
    Nessuna API esterna. Costo per invocazione: zero.

    Restituisce una stringa ~150-200 parole pronta per CLIPTextEncode in ComfyUI.
    """

    # Modalità custom: passa direttamente il testo dell'utente (capped 500 chars)
    if mode == 'custom' and custom_prompt:
        return custom_prompt.strip()[:500]

    # ── Estrai flag audio ─────────────────────────────────────────────────────
    bpm          = float(audio_analysis.get('bpm', 120))
    is_fast      = bool(audio_analysis.get('is_fast', False))
    is_very_fast = bool(audio_analysis.get('is_very_fast', False))
    is_energetic = bool(audio_analysis.get('is_energetic', False))
    is_bright    = bool(audio_analysis.get('is_bright', False))

    # ── Risolvi stile (fallback a cinematic) ─────────────────────────────────
    if style not in _COLOR_SIGS:
        style = 'cinematic'
    brightness_key = 'bright' if is_bright else 'dark'
    energy_key     = 'high'   if is_energetic else 'low'

    # ── Seleziona componenti ciclando su clip_index ───────────────────────────
    shot_type   = _SHOT_TYPES[clip_index % len(_SHOT_TYPES)]
    tier        = _camera_tier(is_very_fast, is_fast, is_energetic)
    cam_motion  = _CAMERA_BANKS[tier][clip_index % len(_CAMERA_BANKS[tier])]
    color_sig   = _COLOR_SIGS[style][brightness_key]
    atmosphere  = _ATMOSPHERE[style][energy_key]
    tempo_lang  = _tempo_language(bpm, is_very_fast, is_fast)
    format_note = _FORMAT_NOTES.get(aspect_ratio, _FORMAT_NOTES['9:16'])
    lyrics_line = _lyrics_anchor(lyrics)

    # ── Assembla il prompt ────────────────────────────────────────────────────
    parts = [
        f"{shot_type}.",
        f"Camera: {cam_motion}.",
        f"Color and light: {color_sig}.",
        f"Atmosphere: {atmosphere}.",
        f"Tempo: {tempo_lang}.",
        f"Composition: {format_note}.",
    ]
    if lyrics_line:
        parts.append(lyrics_line)
    parts.append(
        f"Style: {style}. Cinematic quality, subtle film grain, "
        "professional music video aesthetic. "
        "Subject motion flows naturally from the image — animate what is already there."
    )

    return ' '.join(parts)


# ── Backward-compat alias ─────────────────────────────────────────────────────
# Tutti i caller esistenti usano generate_scene_prompt — nessun refactor necessario.
generate_scene_prompt = build_prompt
