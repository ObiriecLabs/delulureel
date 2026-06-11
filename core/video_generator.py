"""
ComfyUI/RunPod video generation — sostituisce fal.ai al 100%.

Architettura:
  generate_clip(photo_source, prompt, aspect_ratio) — BLOCKING.
  Invia un workflow WAN I2V al RunPod serverless endpoint e fa polling
  finché il video è pronto (caricato su Cloudflare R2 dal worker).
  Restituisce la URL pubblica R2 dell'mp4 generato.

Nessun webhook. Tutta la generazione gira nel thread chiamante (~2-10 min).

Shim di backward-compat mantenuti per gli import di routes.py:
  - transcribe_audio_fal() → restituisce sempre None (Whisper rimosso)
  - fal_result()           → dict vuoto (stub)
  - fal_status_check()     → {'status': 'IN_QUEUE', 'url': None}
  - submit_reel()          → NotImplementedError
  - ENDPOINT_PRO/TURBO     → string stub
  - CLIP_LEN_MULTI         → costante invariata
  - MAX_AUDIO_SEC          → costante invariata

Transcription (fal-ai/whisper) rimossa. I prompt scena si basano su
analisi audio librosa (BPM, energy, beats). Rimpiazzare transcribe_audio_fal()
con Whisper locale se i prompt lyrics-aware tornano necessari.
"""

import os
import math
import random
import base64
import tempfile
import shutil
import time as _time
from typing import Optional

import requests as _req

from core.comfyui_client import (
    submit_workflow_with_images,
    get_status,
    extract_outputs,
)
from core.studio_workflows import (
    load_template,
    substitute,
    randomize_seeds,
    find_nodes_by_class,
)


# ── Costanti (backward compat con routes.py) ──────────────────────────────────

CLIP_LEN_MULTI  = 10    # secondi per clip in generazione multi-clip
CLIP_LEN_SINGLE = 10    # secondi per clip singolo
MAX_AUDIO_SEC   = 600   # cap full-track a 10 min

# Nomi endpoint legacy — tenuti come stub stringa per non rompere gli import.
ENDPOINT_PRO   = 'runpod/wan-i2v'
ENDPOINT_TURBO = 'runpod/wan-i2v'

# Modello costi: RunPod RTX PRO 6000 Blackwell ~$2.19/hr, WAN I2V ~2.5 min/clip
_RUNPOD_COST_PER_MIN  = float(os.getenv('RUNPOD_COST_PER_MIN',  '0.0365'))   # $2.19/60
_CLIP_DURATION_MIN    = float(os.getenv('RUNPOD_CLIP_DURATION_MIN', '2.5'))  # stima media
MARGIN_MULTIPLIER     = float(os.getenv('COST_MARGIN_MULTIPLIER', '2.5'))
USD_TO_EUR            = 0.925

# Polling RunPod
POLL_INTERVAL_SEC = int(os.getenv('RUNPOD_POLL_INTERVAL', '10'))
MAX_WAIT_SEC      = int(os.getenv('RUNPOD_MAX_WAIT_SEC',  '3600'))  # ceiling 1h


# ── Mapping aspect-ratio → dimensioni WAN I2V ────────────────────────────────

_ASPECT_DIMS: dict = {
    '9:16': (480, 832),   # portrait  — AR principale DeluluReel
    '16:9': (832, 480),   # landscape
    '1:1':  (608, 608),   # quadrato — multiplo di 32 compatibile con WAN 2.2
}

# Frames default per aspect ratio (16fps): 81=5s | 121=7.5s | 161=10s (WAN: 4k+1)
_ASPECT_FRAMES: dict = {
    '9:16': 81,
    '16:9': 81,
    '1:1':  81,
}

_DEFAULT_NEGATIVE = (
    "blurry, ugly, deformed, low quality, static, watermark, "
    "duplicate frames, flickering, overexposed, underexposed"
)


# ── Helper costi (backward compat) ───────────────────────────────────────────

def estimate_cost(duration_seconds: int, endpoint: str = ENDPOINT_PRO) -> float:
    """Costo stimato RunPod in USD per duration_seconds di video generato."""
    n_clips = max(1, math.ceil(duration_seconds / CLIP_LEN_MULTI))
    return round(n_clips * _RUNPOD_COST_PER_MIN * _CLIP_DURATION_MIN, 4)


def estimate_user_cost_eur(duration_seconds: int, endpoint: str = ENDPOINT_PRO) -> float:
    """Costo utente in EUR con margine."""
    return round(estimate_cost(duration_seconds) * MARGIN_MULTIPLIER * USD_TO_EUR, 2)


def n_clips_for_duration(target_sec: int, clip_len: int = CLIP_LEN_MULTI) -> int:
    return max(1, math.ceil(target_sec / clip_len))


def endpoint_for_duration(target_sec: int) -> str:
    return ENDPOINT_PRO


# ── Build workflow ────────────────────────────────────────────────────────────

def _build_workflow(
    aspect_ratio: str,
    prompt: str,
    seed: int,
    negative_prompt: str = '',
    num_frames: Optional[int] = None,
):
    """
    Carica il template WAN 2.2 I2V (WanVideoWrapper), inietta dimensioni / prompt / seed.
    Restituisce (workflow_dict, image_filename).
    """
    w, h = _ASPECT_DIMS.get(aspect_ratio, (480, 832))
    n_frames = num_frames or _ASPECT_FRAMES.get(aspect_ratio, 81)
    neg = negative_prompt or _DEFAULT_NEGATIVE

    wf = load_template('wan_i2v')

    # 1. Testo: WanVideoTextEncode ha positive_prompt + negative_prompt nello stesso nodo
    text_nodes = find_nodes_by_class(wf, 'WanVideoTextEncode')
    if text_nodes:
        wf = substitute(wf, {nid: {
            'positive_prompt': prompt,
            'negative_prompt': neg,
        } for nid in text_nodes})

    # 2. Dimensioni + frames sul nodo WanVideoImageToVideoEncode
    i2v_nodes = find_nodes_by_class(wf, 'WanVideoImageToVideoEncode')
    if i2v_nodes:
        wf = substitute(wf, {nid: {
            'width': w, 'height': h, 'num_frames': n_frames,
        } for nid in i2v_nodes})

    # 3. Seed (randomize_seeds gestisce WanVideoSampler automaticamente)
    wf = randomize_seeds(wf, seed)

    # 4. Nome file immagine nel nodo LoadImage
    img_name = 'input_photo.png'
    load_nodes = find_nodes_by_class(wf, 'LoadImage')
    if load_nodes:
        wf = substitute(wf, {load_nodes[0]: {'image': img_name}})

    return wf, img_name


# ── Core generation (blocking) ────────────────────────────────────────────────

def generate_clip(
    photo_source: str,
    prompt: str,
    aspect_ratio: str = '9:16',
    seed: Optional[int] = None,
) -> str:
    """
    Genera un singolo clip video tramite RunPod + ComfyUI (WAN I2V).

    BLOCKING — fa polling di RunPod finché il job è COMPLETED o FAILED.
    Tempo tipico: 2-10 minuti in base al carico GPU.

    Args:
        photo_source: percorso file locale  O  URL http/https dell'immagine.
                      Se è una URL la scarica in un temp file prima di usarla.
        prompt:       descrizione scena (iniettata nel nodo CLIP positivo).
        aspect_ratio: '9:16' (default) | '16:9' | '1:1'
        seed:         casuale se None.

    Returns:
        URL pubblica dell'mp4 generato (Cloudflare R2 via RunPod worker).

    Raises:
        RuntimeError su failure o timeout.
    """
    if seed is None:
        seed = random.randint(0, 2 ** 32 - 1)

    # ── Risolvi photo_source a percorso locale ────────────────────────────────
    _tmp_download = None
    if photo_source.startswith('http://') or photo_source.startswith('https://'):
        _tmp_download = tempfile.mkdtemp(prefix='dlr_img_')
        local_path = os.path.join(_tmp_download, 'photo.jpg')
        try:
            resp = _req.get(photo_source, timeout=60, stream=True)
            resp.raise_for_status()
            with open(local_path, 'wb') as fh:
                for chunk in resp.iter_content(65536):
                    fh.write(chunk)
        except Exception as exc:
            shutil.rmtree(_tmp_download, ignore_errors=True)
            raise RuntimeError(f'Download foto per generazione fallito: {exc}') from exc
    else:
        local_path = photo_source

    try:
        # ── Build workflow ────────────────────────────────────────────────────
        wf, img_name = _build_workflow(aspect_ratio, prompt, seed)

        # ── Foto → base64 ─────────────────────────────────────────────────────
        with open(local_path, 'rb') as fh:
            photo_b64 = base64.b64encode(fh.read()).decode('utf-8')
        images = [{'name': img_name, 'image': photo_b64}]

        # ── Submit a RunPod ───────────────────────────────────────────────────
        print(f'[runpod] submit WAN I2V — ar={aspect_ratio} seed={seed}', flush=True)
        run_id = submit_workflow_with_images(wf, images)
        print(f'[runpod] submitted run_id={run_id}', flush=True)

        # ── Polling fino a terminal ───────────────────────────────────────────
        waited = 0
        while waited < MAX_WAIT_SEC:
            _time.sleep(POLL_INTERVAL_SEC)
            waited += POLL_INTERVAL_SEC

            st     = get_status(run_id)
            status = st.get('status', 'IN_QUEUE')
            print(f'[runpod] {run_id[:12]} status={status} waited={waited}s', flush=True)

            if status == 'COMPLETED':
                outputs = extract_outputs(st)
                # Priorità: output video con URL (R2)
                for out in outputs:
                    if out.get('type') == 'video' and out.get('url'):
                        return out['url']
                # Fallback: qualsiasi output con URL
                for out in outputs:
                    if out.get('url'):
                        return out['url']
                raise RuntimeError(
                    f'RunPod job {run_id} COMPLETED ma nessuna video URL negli output: {outputs}'
                )

            if status in ('FAILED', 'CANCELLED', 'TIMED_OUT'):
                err = ''
                if isinstance(st.get('output'), dict):
                    err = st['output'].get('error', '')
                raise RuntimeError(
                    f'RunPod job {run_id} terminato con status={status}. {err}'
                )

        raise RuntimeError(
            f'RunPod job {run_id} timeout dopo {MAX_WAIT_SEC}s senza COMPLETED'
        )

    finally:
        if _tmp_download:
            shutil.rmtree(_tmp_download, ignore_errors=True)


# ── Shim backward-compat ──────────────────────────────────────────────────────

def submit_reel(
    photo_url: str,
    prompt: str,
    duration: int = 10,
    aspect_ratio: str = '9:16',
    endpoint: str = ENDPOINT_PRO,
    webhook_url: Optional[str] = None,
) -> dict:
    """RIMOSSO — era la funzione di submit fal.ai. Usare generate_clip()."""
    raise NotImplementedError(
        'submit_reel() rimossa. Usare generate_clip(photo_path, prompt, aspect_ratio).'
    )


def fal_result(endpoint: str, request_id: str, response_url: str = '') -> dict:
    """Stub — fal.ai rimosso."""
    return {}


def fal_status_check(
    endpoint: str,
    request_id: str,
    status_url: str = '',
    response_url: str = '',
) -> dict:
    """Stub — fal.ai rimosso."""
    return {'status': 'IN_QUEUE', 'url': None}


def transcribe_audio_fal(audio_url: str) -> Optional[str]:
    """
    Whisper transcription via fal-ai/whisper rimossa (costi + affidabilità).
    I prompt scena si basano ora su analisi audio librosa (BPM, energy, beats).
    Restituisce None — generate_scene_prompt() lo gestisce correttamente.

    TODO: rimpiazzare con Whisper locale o Supabase Edge Function per
          prompt lyrics-aware se necessari in futuro.
    """
    print('[transcribe] Whisper rimosso (fal.ai) — prompt basati su melody/BPM', flush=True)
    return None
