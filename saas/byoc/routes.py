"""
BYOC Test blueprint — pre-subscription hardware compatibility test.

Flusso:
  GET  /byoc/test               → pagina test (no auth)
  POST /byoc/test/detect        → proxy /system_stats + /models dal ComfyUI locale
  POST /byoc/test/start         → valida quota email/IP, avvia job test, ritorna token
  GET  /byoc/test/poll/<token>  → polling status job
  GET  /byoc/test/result/<token>→ output watermarked (immagine/video)

Anti-abuse:
  - Max 3 test per email (permanente)
  - Max 1 test per IP per 24h
  - Token UUID monouso
  - Output servito SOLO via proxy server-side con watermark burned-in
"""
import hashlib
import os
import copy
import random
import urllib.request
import urllib.error
import json
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, render_template, send_file, abort
from supabase import create_client
import io

from core import watermark as wm

byoc_bp = Blueprint("byoc", __name__)

_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# ── Quota limits ──────────────────────────────────────────────────────────────
_MAX_PER_EMAIL  = 3      # totale, permanente
_MAX_PER_IP_24H = 1      # per IP nelle ultime 24h

# ── VRAM requirements per workflow ───────────────────────────────────────────
WORKFLOW_VRAM = {
    "IMAGE_SDXL":          8,
    "IMAGE_FLUX":         16,
    "LTX_480P":           12,
    "LTX_720P":           16,
    "WAN_480P":           24,
    "WAN_720P":           40,
    "AVATAR_INFINITETALK": 12,
}

# Workflow descriptions per la UI del test
WORKFLOW_INFO = {
    "IMAGE_SDXL":          {"name": "SDXL Image",          "icon": "🎨", "note": "512×512 · 10 step"},
    "IMAGE_FLUX":          {"name": "Flux Image",          "icon": "✦",  "note": "512×512 · 10 step"},
    "LTX_480P":            {"name": "LTX Video 480p",      "icon": "🎬", "note": "480p · 5s · 10 step"},
    "LTX_720P":            {"name": "LTX Video 720p",      "icon": "🎬", "note": "720p · 5s · 10 step"},
    "WAN_480P":            {"name": "Wan 2.2 Video 480p",  "icon": "🎞️", "note": "480p · 5s · 10 step"},
    "WAN_720P":            {"name": "Wan 2.2 Video 720p",  "icon": "🎞️", "note": "720p · 5s · 10 step"},
    "AVATAR_INFINITETALK": {"name": "Avatar InfiniteTalk", "icon": "🗣️", "note": "720p · audio-driven"},
}


# ── Minimal test workflow builders ───────────────────────────────────────────

def _wf_sdxl(checkpoint: str, seed: int) -> dict:
    """SDXL test: 512×512, 10 steps — needs CheckpointLoaderSimple."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1],
                         "text": "vibrant test image, studio quality, sharp focus"}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": "low quality, blurry, worst quality"}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0], "seed": seed, "steps": 10,
                         "cfg": 7.0, "sampler_name": "euler",
                         "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "dlr_test"}},
    }


def _wf_flux(model: str, clip_l: str, t5: str, vae: str, seed: int) -> dict:
    """Flux test: 512×512, 10 steps."""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": clip_l, "clip_name2": t5, "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncodeFlux",
              "inputs": {"clip": ["2", 0], "clip_l": "vibrant test image",
                         "t5xxl": "vibrant test image, studio quality, sharp focus",
                         "guidance": 3.5}},
        "5": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "6": {"class_type": "ModelSamplingFlux",
              "inputs": {"model": ["1", 0], "max_shift": 1.15,
                         "base_shift": 0.5, "width": 512, "height": 512}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["6", 0], "positive": ["4", 0], "negative": ["4", 0],
                         "latent_image": ["5", 0], "seed": seed, "steps": 10,
                         "cfg": 1.0, "sampler_name": "euler",
                         "scheduler": "beta", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "dlr_test"}},
    }


def _pick_model(models: list[str], hint: str = "") -> str | None:
    if not models:
        return None
    if hint:
        low = hint.lower()
        for m in models:
            if low in m.lower():
                return m
    return models[0]


def _select_workflow(vram_gb: int, checkpoints: list, diff_models: list,
                     text_encoders: list, vaes: list) -> tuple[str, dict] | tuple[None, None]:
    """
    Sceglie il workflow di test più adatto all'hardware rilevato.
    Restituisce (workflow_id, workflow_dict) oppure (None, None).
    """
    seed = random.randint(1, 2**31)
    vram_unknown = (vram_gb == 0)

    # FLUX (16GB+ con file separati — o VRAM sconosciuta, ci proviamo)
    if vram_unknown or vram_gb >= 16:
        flux_m  = _pick_model(diff_models, "flux")
        flux_cl = _pick_model(text_encoders, "clip_l")
        flux_t5 = _pick_model(text_encoders, "t5")
        flux_v  = _pick_model(vaes, "ae")
        if flux_m and flux_cl and flux_t5 and flux_v:
            return "IMAGE_FLUX", _wf_flux(flux_m, flux_cl, flux_t5, flux_v, seed)

    # SDXL (8GB+ con qualsiasi checkpoint — o VRAM sconosciuta)
    if vram_unknown or vram_gb >= 8:
        ckpt = _pick_model(checkpoints)
        if ckpt:
            return "IMAGE_SDXL", _wf_sdxl(ckpt, seed)

    return None, None


def _quota_ok(email: str, ip: str) -> str | None:
    """Restituisce None se ok, stringa di errore se superata quota."""
    # Email: max 3 totale
    r = _sb.table("byoc_test_sessions").select("id") \
        .eq("email", email.lower()).execute()
    if len(r.data or []) >= _MAX_PER_EMAIL:
        return (f"Hai già effettuato {_MAX_PER_EMAIL} test con questa email. "
                "Abbonati a BYOC per accesso illimitato.")

    # IP: max 1 per 24h
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    r2 = _sb.table("byoc_test_sessions").select("id") \
        .eq("ip", ip) \
        .gte("created_at", cutoff) \
        .execute()
    if len(r2.data or []) >= _MAX_PER_IP_24H:
        return "Hai già eseguito un test nelle ultime 24 ore. Riprova domani."

    return None


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "unknown")


# ── Routes ────────────────────────────────────────────────────────────────────

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "../../static/byoc_test.py")


def _script_source() -> tuple[str, str]:
    """Returns (source_code, sha256_hex) for byoc_test.py."""
    try:
        with open(os.path.abspath(_SCRIPT_PATH)) as f:
            src = f.read()
        sha = hashlib.sha256(src.encode()).hexdigest()
        return src, sha
    except Exception:
        return "", ""


@byoc_bp.route("/test")
def test_page():
    src, sha = _script_source()
    return render_template("byoc/test.html",
                           script_source=src,
                           script_sha256=sha)


@byoc_bp.route("/test/download")
def download_script():
    """Serve byoc_test.py as a file download."""
    path = os.path.abspath(_SCRIPT_PATH)
    if not os.path.exists(path):
        abort(404)
    return send_file(path,
                     mimetype="text/x-python",
                     as_attachment=True,
                     download_name="byoc_test.py")


@byoc_bp.route("/test/latest")
def latest_result():
    """Return the most recent completed test for a given email (for result lookup)."""
    email = request.args.get("email", "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Invalid email"}), 400
    rows = _sb.table("byoc_test_sessions") \
        .select("token, status, created_at") \
        .eq("email", email) \
        .eq("status", "completed") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if not (rows.data):
        return jsonify({"found": False})
    row = rows.data[0]
    return jsonify({"found": True, "token": row["token"]})


@byoc_bp.route("/test/detect", methods=["POST"])
def detect():
    """
    Il browser chiama ComfyUI direttamente (localhost) e ci manda i dati già raccolti.
    Nessuna chiamata server-side a ComfyUI.
    """
    data          = request.get_json(silent=True) or {}
    gpu_name      = (data.get("gpu_name") or "Sconosciuta")[:200]
    vram_total_mb = int(data.get("vram_total_mb") or 0)
    vram_gb       = round(vram_total_mb / 1024) if vram_total_mb else int(data.get("vram_gb") or 0)
    checkpoints   = list(data.get("checkpoints") or [])
    diff_models   = list(data.get("diffusion_models") or [])
    text_encoders = list(data.get("text_encoders") or [])
    vaes          = list(data.get("vae") or [])

    compatible = []
    for wf_id, vram_min in WORKFLOW_VRAM.items():
        info = WORKFLOW_INFO[wf_id]
        compatible.append({
            "id":       wf_id,
            "name":     info["name"],
            "icon":     info["icon"],
            "note":     info["note"],
            "vram_min": vram_min,
            "ok":       vram_gb >= vram_min,
        })

    wf_id, _ = _select_workflow(vram_gb, checkpoints, diff_models, text_encoders, vaes)

    return jsonify({
        "gpu_name":   gpu_name,
        "vram_gb":    vram_gb,
        "compatible": compatible,
        "test_wf":    wf_id,
        "can_test":   wf_id is not None,
    })


@byoc_bp.route("/test/start", methods=["POST"])
def start_test():
    """
    Valida quota, crea sessione, restituisce workflow JSON compilato.
    Il browser esegue il job su ComfyUI locale, poi chiama /finish.
    """
    data          = request.get_json(silent=True) or {}
    email         = (data.get("email") or "").strip().lower()
    gpu_name      = (data.get("gpu_name") or "")[:120]
    vram_total_mb = int(data.get("vram_total_mb") or 0)
    vram_gb       = int(data.get("vram_gb") or 0) or (round(vram_total_mb / 1024) if vram_total_mb else 0)
    checkpoints   = list(data.get("checkpoints") or [])
    diff_models   = list(data.get("diffusion_models") or [])
    text_encoders = list(data.get("text_encoders") or [])
    vaes          = list(data.get("vae") or [])

    if not email or "@" not in email:
        return jsonify({"error": "Email non valida."}), 400

    ip  = _client_ip()
    err = _quota_ok(email, ip)
    if err:
        return jsonify({"error": err}), 429

    wf_id, workflow = _select_workflow(vram_gb, checkpoints, diff_models, text_encoders, vaes)
    if not workflow:
        return jsonify({
            "error": "Nessun modello compatibile trovato nel tuo ComfyUI. "
                     "Scarica almeno un checkpoint SDXL o i file FLUX per eseguire il test."
        }), 422

    comfyui_url = (data.get("comfyui_url") or "http://localhost:8188")[:200]
    row = _sb.table("byoc_test_sessions").insert({
        "email":        email,
        "ip":           ip,
        "gpu_name":     gpu_name,
        "vram_gb":      vram_gb,
        "comfyui_url":  comfyui_url,
        "workflow_id":  wf_id,
        "status":       "running",
    }).execute()
    token = row.data[0]["token"]

    return jsonify({"token": token, "workflow": workflow, "workflow_id": wf_id})


@byoc_bp.route("/test/finish", methods=["POST"])
def finish_test():
    """
    Il browser ha completato la generazione locale e carica il file raw.
    Il server applica il watermark, salva su Supabase Storage, marca completed.
    """
    token = request.form.get("token", "").strip()
    f     = request.files.get("file")
    if not token or not f:
        return jsonify({"error": "token o file mancante"}), 400

    row = _sb.table("byoc_test_sessions") \
        .select("id, status") \
        .eq("token", token).maybe_single().execute()
    if not row.data:
        return jsonify({"error": "Token non valido."}), 404
    if row.data["status"] not in ("running", "pending"):
        return jsonify({"error": "Sessione già completata."}), 409

    raw      = f.read()
    fname    = f.filename or "output"
    ext      = fname.rsplit(".", 1)[-1].lower() if "." in fname else "png"
    is_video = ext in ("mp4", "webm")

    if is_video:
        final = wm.watermark_video(raw)
        ctype = "video/mp4"
        ext   = "mp4"
    else:
        final = wm.watermark_image(raw)
        ctype = "image/png"
        ext   = "png"

    storage_path = f"byoc-tests/{token}.{ext}"
    _sb.storage.from_("reel-outputs").upload(
        storage_path, final, file_options={"content-type": ctype}
    )
    result_url = _sb.storage.from_("reel-outputs").get_public_url(storage_path)

    _sb.table("byoc_test_sessions").update({
        "status": "completed",
        "job_id": result_url,
    }).eq("token", token).execute()

    return jsonify({"ok": True})


@byoc_bp.route("/test/poll/<token>")
def poll_test(token: str):
    """Polling status del job test."""
    row = _sb.table("byoc_test_sessions").select("status") \
        .eq("token", token).maybe_single().execute()
    if not row.data:
        return jsonify({"error": "Token non valido."}), 404
    return jsonify({"status": row.data["status"]})


@byoc_bp.route("/test/result/<token>")
def result_test(token: str):
    """Serve il file watermarked salvato da finish_test() su Supabase Storage."""
    row = _sb.table("byoc_test_sessions") \
        .select("status, job_id") \
        .eq("token", token).maybe_single().execute()
    if not row.data:
        abort(404)

    s = row.data
    if s["status"] != "completed":
        return jsonify({"status": s["status"]}), 202

    result_url = s.get("job_id", "")
    if not result_url or not result_url.startswith("http"):
        abort(404)

    req = urllib.request.Request(result_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw   = resp.read()
        ctype = resp.headers.get("Content-Type", "image/png")

    ext = "mp4" if "video" in ctype else "png"
    return send_file(
        io.BytesIO(raw),
        mimetype=ctype,
        as_attachment=False,
        download_name=f"delulureel_test.{ext}",
    )
