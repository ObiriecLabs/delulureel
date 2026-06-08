"""
DELULUREEL Studio — Blueprint Flask
Gestisce job ComfyUI su RunPod: submit, status, lista, coda.

Endpoint unico: RTX PRO 6000 Blackwell (96 GB VRAM) — EUR-IS-1
Tier access:
  creator → workflow base (IMAGE, LTX 480p)
  pro     → workflow avanzati (LTX 720p, Wan 480p)
  studio  → tutti i workflow (Wan 720p, Avatar)
"""
import threading
import base64
import random
import io
from flask import Blueprint, request, jsonify, render_template
from supabase import create_client
import os

from core import comfyui_client as cc
from core import studio_workflows as sw
from saas.auth.routes import require_auth

studio_bp = Blueprint("studio", __name__, url_prefix="/studio")

_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# Workflow catalog — mostrato nella UI Studio
WORKFLOW_CATALOG = [
    {
        "id": "IMAGE_FLUX",
        "name": "Flux Image",
        "description": "Immagini realistiche ad alta fedeltà con Flux Krea Dev fp8. Ritratti, paesaggi, concept art.",
        "category": "image",
        "resolution": "fino a 4K",
        "gpu": "RTX PRO 6000",
        "tier_min": "creator",
        "icon": "✦",
    },
    {
        "id": "IMAGE_SDXL",
        "name": "SDXL Image",
        "description": "Multi-stile: Juggernaut, RealVis, CyberRealistic, Pony. Il checkpoint più adatto al soggetto.",
        "category": "image",
        "resolution": "fino a 2048×2048",
        "gpu": "RTX PRO 6000",
        "tier_min": "creator",
        "icon": "🎨",
    },
    {
        "id": "LTX_480P",
        "name": "LTX Video 480p",
        "description": "LTX 2.3 (22B fp8) — broadcast quality nativo. Audio-sync, motion fluido. Preview resolution.",
        "category": "video",
        "resolution": "480p · 5–10s",
        "gpu": "RTX PRO 6000",
        "tier_min": "creator",
        "icon": "🎬",
    },
    {
        "id": "LTX_720P",
        "name": "LTX Video 720p",
        "description": "LTX 2.3 in HD — standard per cortometraggi e Patreon NODEVAULT.",
        "category": "video",
        "resolution": "720p · 5–10s",
        "gpu": "RTX PRO 6000",
        "tier_min": "pro",
        "icon": "🎬",
    },
    {
        "id": "WAN_480P",
        "name": "Wan 2.2 Video 480p",
        "description": "Wan 2.2 T2V/I2V — motion ad alto dettaglio, stili cinematografici multipli.",
        "category": "video",
        "resolution": "480p · 5–8s",
        "gpu": "RTX PRO 6000",
        "tier_min": "pro",
        "icon": "🎞️",
    },
    {
        "id": "WAN_720P",
        "name": "Wan 2.2 Video 720p",
        "description": "Wan 2.2 14B fp8 in HD su H100 — massima qualità motion disponibile.",
        "category": "video",
        "resolution": "720p · 5–8s",
        "gpu": "RTX PRO 6000",
        "tier_min": "studio",
        "icon": "🎞️",
    },
    {
        "id": "AVATAR_INFINITETALK",
        "name": "Avatar InfiniteTalk",
        "description": "Talking-head AI guidato da audio. Wan 2.1 + InfiniteTalk Q8. Lip-sync da qualsiasi traccia.",
        "category": "avatar",
        "resolution": "720p · audio-driven",
        "gpu": "RTX PRO 6000",
        "tier_min": "studio",
        "icon": "🗣️",
    },
]

_TIER_ORDER = {"creator": 0, "pro": 1, "studio": 2, "byoc": 99}


def _tier_accessible(user_tier: str, required_tier: str) -> bool:
    return _TIER_ORDER.get(user_tier, 0) >= _TIER_ORDER.get(required_tier, 0)


# Tier → endpoint + workflow ammessi
TIER_CONFIG = {
    "byoc": {
        "gpu": "Local GPU",
        "max_concurrent": 10,
        "allowed_workflows": ["*"],
    },
    "creator": {
        "gpu": "RTX PRO 6000",
        "max_concurrent": 1,
        "allowed_workflows": ["IMAGE_SDXL", "IMAGE_FLUX", "LTX_480P"],
    },
    "pro": {
        "gpu": "RTX PRO 6000",
        "max_concurrent": 2,
        "allowed_workflows": ["IMAGE_SDXL", "IMAGE_FLUX", "LTX_720P", "WAN_480P"],
    },
    "studio": {
        "gpu": "RTX PRO 6000",
        "max_concurrent": 5,
        "allowed_workflows": ["*"],
    },
}


def _get_user_config(user_id: str) -> dict:
    """Restituisce {tier, is_admin, comfyui_mode, comfyui_local_url}."""
    row = _sb.table("profiles") \
        .select("plan, status, is_admin, comfyui_mode, comfyui_local_url") \
        .eq("user_id", user_id).single().execute()
    data = row.data or {}
    is_admin = bool(data.get("is_admin"))
    plan = data.get("plan", "")
    if is_admin or "studio" in plan:
        tier = "studio"
    elif "byoc" in plan:
        tier = "byoc"
    elif "pro" in plan:
        tier = "pro"
    else:
        tier = "creator"
    default_mode = "local" if tier == "byoc" else "runpod"
    return {
        "tier": tier,
        "is_admin": is_admin,
        "comfyui_mode": data.get("comfyui_mode") or default_mode,
        "comfyui_local_url": data.get("comfyui_local_url") or "",
    }


def _active_jobs_count(user_id: str) -> int:
    rows = (
        _sb.table("studio_jobs")
        .select("id")
        .eq("user_id", user_id)
        .in_("status", ["queued", "running"])
        .execute()
    )
    return len(rows.data or [])


def _run_job_background(job_id: str, workflow: dict, tier: str, mode: str = "runpod", local_url: str = ""):
    """Thread: invia al backend ComfyUI, polling, aggiorna Supabase, salva output."""
    try:
        run_id = cc.submit_workflow(workflow, tier, mode=mode, local_url=local_url or None)
        _sb.table("studio_jobs").update({"runpod_id": run_id, "status": "running"}).eq("id", job_id).execute()

        while True:
            import time
            time.sleep(4)
            status_dict = cc.get_status(run_id, tier, mode=mode, local_url=local_url or None)
            runpod_status = status_dict.get("status", "IN_QUEUE")

            if runpod_status == "COMPLETED":
                outputs = cc.extract_outputs(status_dict)
                gpu_ms = status_dict.get("executionTime", 0)

                # Il worker carica già su object storage (R2) e restituisce 'url'.
                # Fallback: se un output arriva in base64 ('data') — solo test —
                # lo carichiamo su Supabase Storage.
                saved_urls = []
                for i, out in enumerate(outputs):
                    if out.get("url"):
                        saved_urls.append({
                            "filename": out["filename"],
                            "url": out["url"],
                            "type": out["type"],
                        })
                    elif out.get("data"):
                        ext = "mp4" if out["type"] == "video" else "png"
                        storage_path = f"studio/{job_id}/output_{i}.{ext}"
                        binary = base64.b64decode(out["data"])
                        _sb.storage.from_("reel-outputs").upload(
                            storage_path, binary,
                            file_options={"content-type": f"{'video/mp4' if ext == 'mp4' else 'image/png'}"}
                        )
                        url = _sb.storage.from_("reel-outputs").get_public_url(storage_path)
                        saved_urls.append({"filename": out["filename"], "url": url, "type": out["type"]})

                _sb.table("studio_jobs").update({
                    "status": "completed",
                    "output_urls": saved_urls,
                    "gpu_seconds": round(gpu_ms / 1000, 2),
                }).eq("id", job_id).execute()
                break

            elif cc.is_terminal(runpod_status):
                _sb.table("studio_jobs").update({
                    "status": "failed",
                    "error": runpod_status,
                }).eq("id", job_id).execute()
                break

    except Exception as e:
        _sb.table("studio_jobs").update({"status": "failed", "error": str(e)}).eq("id", job_id).execute()


# ── Routes ────────────────────────────────────────────────────────────────────

@studio_bp.route("/generate", methods=["POST"])
@require_auth
def generate():
    """
    POST /studio/generate
    Body: { "workflow": {...}, "workflow_name": "LTX_720P" }
    """
    user_id = request.current_user.id
    data = request.get_json(force=True) or {}
    workflow = data.get("workflow")
    workflow_name = data.get("workflow_name", "custom")

    if not workflow:
        return jsonify({"error": "Missing workflow"}), 400

    ucfg = _get_user_config(user_id)
    tier  = ucfg["tier"]
    mode  = ucfg["comfyui_mode"]
    lurl  = ucfg["comfyui_local_url"]
    cfg   = TIER_CONFIG.get(tier, TIER_CONFIG["creator"])

    if _active_jobs_count(user_id) >= cfg["max_concurrent"]:
        return jsonify({"error": "Max concurrent jobs reached for your plan"}), 429

    allowed = cfg["allowed_workflows"]
    if allowed != ["*"] and workflow_name not in allowed:
        return jsonify({"error": f"Workflow {workflow_name} not available on {tier} plan"}), 403

    row = _sb.table("studio_jobs").insert({
        "user_id": user_id,
        "tier": tier,
        "workflow_name": workflow_name,
        "status": "queued",
        "credits_used": 1,
        "backend_mode": mode,
        "backend_url": lurl or None,
    }).execute()
    job_id = row.data[0]["id"]

    t = threading.Thread(
        target=_run_job_background,
        args=(job_id, workflow, tier, mode, lurl),
        daemon=True,
    )
    t.start()

    queue_pos = cc.queue_depth(tier, mode=mode, local_url=lurl or None)
    wait_sec  = cc.estimate_wait_seconds(tier, mode=mode, local_url=lurl or None)

    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "queue_position": queue_pos,
        "estimated_wait_seconds": wait_sec,
        "backend": mode,
    }), 202


@studio_bp.route("/status/<job_id>", methods=["GET"])
@require_auth
def status(job_id):
    """GET /studio/status/<job_id> — polling status + output quando completato."""
    user_id = request.current_user.id
    row = (
        _sb.table("studio_jobs")
        .select("*")
        .eq("id", job_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not row.data:
        return jsonify({"error": "Job not found"}), 404

    job = row.data
    resp = {
        "job_id": job_id,
        "status": job["status"],
        "workflow_name": job["workflow_name"],
        "created_at": job["created_at"],
        "gpu_seconds": job.get("gpu_seconds"),
    }

    if job["status"] == "completed":
        resp["outputs"] = job.get("output_urls", [])
    elif job["status"] in ("queued", "running") and job.get("runpod_id"):
        # Live queue depth per stima attesa
        resp["queue_position"] = cc.queue_depth(job["tier"])
        resp["estimated_wait_seconds"] = cc.estimate_wait_seconds(job["tier"])
    elif job["status"] == "failed":
        resp["error"] = job.get("error")

    return jsonify(resp)


@studio_bp.route("/jobs", methods=["GET"])
@require_auth
def list_jobs():
    """GET /studio/jobs — ultimi 50 job dell'utente."""
    user_id = request.current_user.id
    rows = (
        _sb.table("studio_jobs")
        .select("id, status, workflow_name, created_at, gpu_seconds, output_urls")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return jsonify({"jobs": rows.data or []})


@studio_bp.route("/queue", methods=["GET"])
@require_auth
def queue_info():
    user_id = request.current_user.id
    ucfg = _get_user_config(user_id)
    tier, mode, lurl = ucfg["tier"], ucfg["comfyui_mode"], ucfg["comfyui_local_url"]
    return jsonify({
        "tier": tier,
        "gpu": TIER_CONFIG[tier]["gpu"],
        "queue_depth": cc.queue_depth(tier, mode=mode, local_url=lurl or None),
        "estimated_wait_seconds": cc.estimate_wait_seconds(tier, mode=mode, local_url=lurl or None),
        "backend": mode,
    })


# ── ComfyUI local settings (per-utente) ──────────────────────────────────────

@studio_bp.route("/settings/local", methods=["GET"])
@require_auth
def get_local_settings():
    """GET /studio/settings/local — legge mode e local_url dell'utente."""
    user_id = request.current_user.id
    ucfg = _get_user_config(user_id)
    return jsonify({
        "comfyui_mode": ucfg["comfyui_mode"],
        "comfyui_local_url": ucfg["comfyui_local_url"],
    })


@studio_bp.route("/settings/local", methods=["POST"])
@require_auth
def save_local_settings():
    """
    POST /studio/settings/local
    Body: { "comfyui_mode": "local"|"runpod", "comfyui_local_url": "https://..." }
    """
    user_id = request.current_user.id
    data = request.get_json(force=True) or {}
    mode = data.get("comfyui_mode", "runpod")
    lurl = (data.get("comfyui_local_url") or "").strip().rstrip("/")

    if mode not in ("runpod", "local"):
        return jsonify({"error": "Mode non valido"}), 400
    if mode == "local" and not lurl:
        return jsonify({"error": "Inserisci l'URL del tuo ComfyUI locale"}), 400

    _sb.table("profiles").update({
        "comfyui_mode": mode,
        "comfyui_local_url": lurl or None,
    }).eq("user_id", user_id).execute()

    return jsonify({"comfyui_mode": mode, "comfyui_local_url": lurl})


@studio_bp.route("/settings/ping", methods=["POST"])
@require_auth
def ping_local():
    """POST /studio/settings/ping — verifica che l'URL ComfyUI locale risponda."""
    data = request.get_json(force=True) or {}
    lurl = (data.get("url") or "").strip().rstrip("/")
    if not lurl:
        return jsonify({"ok": False, "error": "URL mancante"}), 400
    ok = cc.ping_local(lurl)
    return jsonify({"ok": ok, "url": lurl})


# ── Page routes ───────────────────────────────────────────────────────────────

@studio_bp.route("/", methods=["GET"])
@require_auth
def studio_index():
    user_id = request.current_user.id
    ucfg = _get_user_config(user_id)
    return render_template(
        "studio/index.html",
        user_tier=ucfg["tier"],
        workflows=WORKFLOW_CATALOG,
        is_admin=ucfg["is_admin"],
        comfyui_mode=ucfg["comfyui_mode"],
        comfyui_local_url=ucfg["comfyui_local_url"],
    )


@studio_bp.route("/job/<job_id>", methods=["GET"])
@require_auth
def studio_job_page(job_id):
    """GET /studio/job/<job_id> — pagina stato/risultato del job."""
    return render_template("studio/job.html", job_id=job_id)


# ── Name-based submit ─────────────────────────────────────────────────────────

@studio_bp.route("/submit", methods=["POST"])
@require_auth
def submit():
    """
    POST /studio/submit
    Body: { "workflow_name": "LTX_720P", "prompt": "...", "seed": 42 }
    Carica il template server-side, inietta prompt/seed, invia a RunPod.
    """
    user_id = request.current_user.id
    data = request.get_json(force=True) or {}
    workflow_name = data.get("workflow_name", "").strip()
    prompt = data.get("prompt", "").strip()
    seed = data.get("seed")

    if not workflow_name:
        return jsonify({"error": "Missing workflow_name"}), 400

    catalog_entry = next((w for w in WORKFLOW_CATALOG if w["id"] == workflow_name), None)
    if not catalog_entry:
        return jsonify({"error": "Workflow sconosciuto"}), 400

    ucfg = _get_user_config(user_id)
    tier  = ucfg["tier"]
    mode  = ucfg["comfyui_mode"]
    lurl  = ucfg["comfyui_local_url"]

    if not _tier_accessible(tier, catalog_entry["tier_min"]):
        return jsonify({
            "error": f"Questo workflow richiede il piano {catalog_entry['tier_min'].capitalize()}."
        }), 403

    cfg = TIER_CONFIG.get(tier, TIER_CONFIG["creator"])
    if _active_jobs_count(user_id) >= cfg["max_concurrent"]:
        return jsonify({"error": "Limite job simultanei raggiunto per il tuo piano."}), 429

    try:
        workflow = sw.load_template(workflow_name)
    except FileNotFoundError:
        return jsonify({
            "error": (
                f"Template '{workflow_name}' non ancora configurato. "
                "Esegui: python tools/capture_api_workflow.py capture " + workflow_name
            )
        }), 404

    if not sw.is_api_format(workflow):
        return jsonify({"error": "Il template non è in formato API ComfyUI."}), 500

    if prompt:
        workflow = sw.apply_prompt(workflow, prompt)
    actual_seed = int(seed) if seed is not None else random.randint(1, 2 ** 31 - 1)
    workflow = sw.randomize_seeds(workflow, actual_seed)

    # ── Advanced overrides (optional — sent by Custom workflow UI) ──────────
    adv_steps     = data.get("steps")
    adv_cfg       = data.get("cfg")
    adv_sampler   = (data.get("sampler_name") or "").strip()
    adv_scheduler = (data.get("scheduler")    or "").strip()
    adv_negative  = (data.get("negative_prompt") or "").strip()
    adv_width     = data.get("width")
    adv_height    = data.get("height")

    # KSampler overrides
    ksampler_ovr = {}
    if adv_steps  is not None: ksampler_ovr["steps"]        = max(1, min(int(adv_steps), 150))
    if adv_cfg    is not None: ksampler_ovr["cfg"]          = max(0.0, min(float(adv_cfg), 20.0))
    if adv_sampler:            ksampler_ovr["sampler_name"] = adv_sampler
    if adv_scheduler:          ksampler_ovr["scheduler"]    = adv_scheduler
    if ksampler_ovr:
        for cls in ("KSampler", "KSamplerAdvanced", "LTXVSampler", "WanVideoSampler"):
            for nid in sw.find_nodes_by_class(workflow, cls):
                try: workflow = sw.substitute(workflow, {nid: ksampler_ovr})
                except KeyError: pass

    # Negative prompt
    _NEG = frozenset({"blurry", "ugly", "low quality", "bad", "worst", "nsfw", "watermark", "deformed"})
    if adv_negative:
        for nid, node in workflow.items():
            if not isinstance(node, dict): continue
            if node.get("class_type") not in ("CLIPTextEncode", "CLIPTextEncodeFlux", "WanTextEncode"):
                continue
            inp = node.get("inputs", {})
            existing = (inp.get("text") or inp.get("clip_l") or inp.get("t5xxl") or "").lower()
            if any(h in existing for h in _NEG):
                try: workflow = sw.substitute(workflow, {nid: {"text": adv_negative}})
                except KeyError: pass

    # Resolution
    if adv_width and adv_height:
        w = max(64, min(int(adv_width), 2048))
        h = max(64, min(int(adv_height), 2048))
        for cls in ("EmptyLatentImage", "EmptySD3LatentImage"):
            for nid in sw.find_nodes_by_class(workflow, cls):
                try: workflow = sw.substitute(workflow, {nid: {"width": w, "height": h}})
                except KeyError: pass

    row = _sb.table("studio_jobs").insert({
        "user_id": user_id,
        "tier": tier,
        "workflow_name": workflow_name,
        "status": "queued",
        "credits_used": 1,
        "backend_mode": mode,
        "backend_url": lurl or None,
    }).execute()
    job_id = row.data[0]["id"]

    if mode == "local":
        # Workflow eseguito lato browser: restituiamo il JSON compilato
        return jsonify({"job_id": job_id, "mode": "local", "workflow": workflow}), 202

    t = threading.Thread(
        target=_run_job_background,
        args=(job_id, workflow, tier, mode, lurl),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "queued", "backend": mode}), 202


@studio_bp.route("/job/save", methods=["POST"])
@require_auth
def save_job():
    """
    POST /studio/job/save — riceve i file output dal browser (local BYOC mode),
    li carica su Supabase Storage e marca il job come completato.
    FormData: job_id + uno o più file con chiave 'files'.
    """
    user_id = request.current_user.id
    job_id  = request.form.get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "job_id mancante"}), 400

    row = (
        _sb.table("studio_jobs")
        .select("id, user_id, workflow_name")
        .eq("id", job_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not row.data:
        return jsonify({"error": "Job non trovato"}), 404

    saved_urls = []
    for f in request.files.getlist("files"):
        fname = f.filename or "output"
        ext   = fname.rsplit(".", 1)[-1].lower() if "." in fname else "png"
        ctype = f.content_type or ("video/mp4" if ext in ("mp4", "webm") else "image/png")
        ftype = "video" if ext in ("mp4", "webm", "webp") else "image"
        path  = f"studio/{job_id}/{fname}"
        data  = f.read()
        _sb.storage.from_("reel-outputs").upload(
            path, data, file_options={"content-type": ctype}
        )
        url = _sb.storage.from_("reel-outputs").get_public_url(path)
        saved_urls.append({"filename": fname, "url": url, "type": ftype})

    _sb.table("studio_jobs").update({
        "status":      "completed",
        "output_urls": saved_urls,
    }).eq("id", job_id).execute()

    return jsonify({"job_id": job_id, "outputs": saved_urls})
