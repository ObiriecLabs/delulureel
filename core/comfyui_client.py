"""
RunPod ComfyUI client per DELULUREEL.
Pattern identico a video_generator.py (fal.ai), adattato per RunPod Serverless.

Endpoint unico: RTX PRO 6000 Blackwell (96 GB VRAM) — EUR-IS-1
Tutti i tier (creator / pro / studio) usano lo stesso endpoint.

mode="runpod"  → chiama RunPod Serverless API
mode="local"   → chiama ComfyUI locale direttamente (BYOC)
"""
import os
import json
import urllib.request
import urllib.error

RUNPOD_API_KEY  = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "")
_BASE = "https://api.runpod.ai/v2"

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
    }


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{_BASE}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _local_request(method: str, local_url: str, path: str, body: dict | None = None) -> dict:
    url = f"{local_url.rstrip('/')}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ── Public API ────────────────────────────────────────────────────────────────

def submit_workflow(
    workflow: dict,
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> str:
    """
    Invia un workflow ComfyUI (API format) al backend.
    RunPod: restituisce run_id RunPod.
    Local:  restituisce prompt_id ComfyUI.
    """
    if mode == "local" and local_url:
        import uuid
        client_id = str(uuid.uuid4())
        result = _local_request(
            "POST", local_url, "/prompt",
            {"prompt": workflow, "client_id": client_id},
        )
        return result["prompt_id"]
    result = _request("POST", f"{RUNPOD_ENDPOINT}/run", {"input": {"workflow": workflow}})
    return result["id"]


def submit_workflow_with_images(
    workflow: dict,
    images: list[dict],
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> str:
    """
    Come submit_workflow ma con immagini di input base64.
    images = [{"name": "ref.png", "data": "<base64>"}]
    """
    if mode == "local" and local_url:
        return submit_workflow(workflow, tier, mode=mode, local_url=local_url)
    result = _request(
        "POST",
        f"{RUNPOD_ENDPOINT}/run",
        {"input": {"workflow": workflow, "images": images}},
    )
    return result["id"]


def get_status(
    run_id: str,
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> dict:
    """
    Restituisce lo status dict del job.
    RunPod: status IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED | ...
    Local:  polling /history/<prompt_id>, status sintetizzato.
    """
    if mode == "local" and local_url:
        try:
            data = _local_request("GET", local_url, f"/history/{run_id}")
            if run_id in data:
                history = data[run_id]
                outputs = history.get("outputs", {})
                return {"status": "COMPLETED", "output": {"outputs": _flatten_local_outputs(outputs)}}
            return {"status": "IN_QUEUE"}
        except Exception:
            return {"status": "IN_QUEUE"}
    return _request("GET", f"{RUNPOD_ENDPOINT}/status/{run_id}")


def _flatten_local_outputs(outputs: dict) -> list:
    """Converte outputs ComfyUI locale nel formato atteso dal backend."""
    result = []
    for node_output in outputs.values():
        for media_key in ("images", "videos", "gifs"):
            for f in node_output.get(media_key, []):
                kind = "video" if media_key in ("videos", "gifs") else "image"
                result.append({
                    "filename": f["filename"],
                    "subfolder": f.get("subfolder", ""),
                    "type": kind,
                })
    return result


def cancel_job(
    run_id: str,
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> None:
    if mode == "local":
        return
    _request("POST", f"{RUNPOD_ENDPOINT}/cancel/{run_id}")


def queue_depth(
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> int:
    if mode == "local":
        return 0
    if not RUNPOD_ENDPOINT:
        return -1
    try:
        data = _request("GET", f"{RUNPOD_ENDPOINT}/health")
        return data.get("jobs", {}).get("inQueue", 0)
    except Exception:
        return -1


def estimate_wait_seconds(
    tier: str = "creator",
    mode: str = "runpod",
    local_url: str | None = None,
) -> int:
    if mode == "local":
        return 0
    avg_job_sec = {"creator": 30, "pro": 90, "studio": 180}
    depth = queue_depth(tier, mode=mode, local_url=local_url)
    if depth < 0:
        return 0
    return depth * avg_job_sec.get(tier, 60)


def ping_local(local_url: str) -> bool:
    """Verifica che un ComfyUI locale risponda."""
    try:
        _local_request("GET", local_url, "/system_stats")
        return True
    except Exception:
        return False


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def extract_outputs(status_dict: dict) -> list[dict]:
    """
    Estrae la lista di output da un job COMPLETED.
    Ogni elemento: {"filename": str, "type": "image"|"video", "url": str} (RunPod+R2)
                   oppure {"filename": str, "type": ..., "data": "<base64>"} (fallback base64)
    """
    return status_dict.get("output", {}).get("outputs", [])
