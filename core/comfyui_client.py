"""
RunPod ComfyUI client per DELULUREEL.
Pattern identico a video_generator.py (fal.ai), adattato per RunPod Serverless.

Endpoint unico: RTX PRO 6000 Blackwell (96 GB VRAM) — EUR-IS-1
Tutti i tier (creator / pro / studio) usano lo stesso endpoint.
"""
import os
import json
import urllib.request
import urllib.error

RUNPOD_API_KEY   = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT  = os.environ.get("RUNPOD_ENDPOINT", "")
_BASE = "https://api.runpod.ai/v2"

# Status RunPod: IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED | CANCELLED | TIMED_OUT
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _endpoint(tier: str = "creator") -> str:
    return RUNPOD_ENDPOINT


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


# ── Public API ────────────────────────────────────────────────────────────────

def submit_workflow(workflow: dict, tier: str = "creator") -> str:
    """
    Invia un workflow ComfyUI (LiteGraph JSON) a RunPod.
    Restituisce il run_id RunPod da salvare in Supabase.
    """
    endpoint = _endpoint(tier)
    result = _request("POST", f"{endpoint}/run", {"input": {"workflow": workflow}})
    return result["id"]


def submit_workflow_with_images(
    workflow: dict,
    images: list[dict],
    tier: str = "creator",
) -> str:
    """
    Come submit_workflow ma con immagini di input base64.
    images = [{"name": "ref.png", "data": "<base64>"}]
    """
    endpoint = _endpoint(tier)
    result = _request(
        "POST",
        f"{endpoint}/run",
        {"input": {"workflow": workflow, "images": images}},
    )
    return result["id"]


def get_status(run_id: str, tier: str = "creator") -> dict:
    """
    Restituisce lo status dict RunPod.
    Campi rilevanti:
      status: IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED | ...
      output: {"outputs": [{"filename": ..., "type": ..., "data": <base64>}]}
      delayTime: ms passati in coda
      executionTime: ms di GPU usati
    """
    endpoint = _endpoint(tier)
    return _request("GET", f"{endpoint}/status/{run_id}")


def cancel_job(run_id: str, tier: str = "creator") -> None:
    """Cancella un job in coda o in esecuzione."""
    endpoint = _endpoint(tier)
    _request("POST", f"{endpoint}/cancel/{run_id}")


def queue_depth(tier: str = "creator") -> int:
    """Numero di job attualmente in coda per l'endpoint."""
    endpoint = _endpoint(tier)
    try:
        data = _request("GET", f"{endpoint}/health")
        return data.get("jobs", {}).get("inQueue", 0)
    except Exception:
        return -1


def estimate_wait_seconds(tier: str = "creator") -> int:
    """
    Stima rozza del tempo di attesa in secondi.
    Basata su: job in coda × tempo medio per job (tier-specific).
    """
    avg_job_sec = {"creator": 30, "pro": 90, "studio": 180}
    depth = queue_depth(tier)
    if depth < 0:
        return 0
    return depth * avg_job_sec.get(tier, 60)


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def extract_outputs(status_dict: dict) -> list[dict]:
    """
    Estrae la lista di output da un job COMPLETED.
    Ogni elemento: {"filename": str, "type": "image"|"video", "data": "<base64>"}
    """
    return status_dict.get("output", {}).get("outputs", [])
