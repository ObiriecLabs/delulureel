"""
RunPod Serverless Handler — ComfyUI Worker
Avvia ComfyUI internamente, riceve workflow in formato API, restituisce output.

IMPORTANTE:
  - Il workflow DEVE essere in formato API ComfyUI (Export API), non LiteGraph UI.
  - Gli output (immagini e SOPRATTUTTO video mp4) vengono caricati su object storage
    S3-compatible (Cloudflare R2 / Supabase / Backblaze) e restituiti come URL.
    Motivo: limiti risposta RunPod (/run ~10MB, /runsync ~20MB) → un mp4 in base64 sfora.
  - Fallback base64 SOLO se lo storage S3 non è configurato (utile per test locali).
"""
import runpod
import subprocess
import sys
import time
import json
import urllib.request
import base64
import os
import uuid
import shutil
import mimetypes

# COMFYUI_LISTEN=0.0.0.0 per pod dedicato RunPod con porta 8188 esposta
# (necessario per comfyui-mcp remote mode — mai impostare su worker serverless)
COMFYUI_HOST = os.environ.get("COMFYUI_LISTEN", "127.0.0.1")
COMFYUI_PORT = int(os.environ.get("COMFYUI_PORT", "8188"))
COMFYUI_URL = f"http://{COMFYUI_HOST}:{COMFYUI_PORT}"
INPUT_DIR = "/comfyui/input"
OUTPUT_DIR = "/comfyui/output"

# ── S3 / object storage config (env sull'endpoint RunPod) ─────────────────────
BUCKET_ENDPOINT_URL    = os.environ.get("BUCKET_ENDPOINT_URL", "")
BUCKET_ACCESS_KEY_ID   = os.environ.get("BUCKET_ACCESS_KEY_ID", "")
BUCKET_SECRET_ACCESS_KEY = os.environ.get("BUCKET_SECRET_ACCESS_KEY", "")
BUCKET_NAME            = os.environ.get("BUCKET_NAME", "")
BUCKET_PUBLIC_URL      = os.environ.get("BUCKET_PUBLIC_URL", "").rstrip("/")

_s3 = None
if BUCKET_ENDPOINT_URL and BUCKET_ACCESS_KEY_ID and BUCKET_NAME:
    import boto3
    _s3 = boto3.client(
        "s3",
        endpoint_url=BUCKET_ENDPOINT_URL,
        aws_access_key_id=BUCKET_ACCESS_KEY_ID,
        aws_secret_access_key=BUCKET_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    print("[BOOT] S3 output storage configured.")
else:
    print("[BOOT] No S3 configured — outputs will be base64 (test mode only).")

# ── ComfyUI lifecycle ─────────────────────────────────────────────────────────

_COMFYUI_LOG = "/tmp/comfyui.log"

def _start_comfyui():
    # sys.executable: punta sempre all'interprete corrente (funziona in conda e venv)
    cmd = [
        sys.executable, "/comfyui/main.py",
        "--listen", COMFYUI_HOST,
        "--port", str(COMFYUI_PORT),
        "--disable-auto-launch",
        # --cuda-malloc rimosso: causa hang silenzioso su Blackwell (SM 10.x)
        "--extra-model-paths-config", "/comfyui/extra_model_paths.yaml",
    ]
    log_f = open(_COMFYUI_LOG, "w")
    return subprocess.Popen(cmd, stdout=log_f, stderr=log_f)

def _tail_comfyui_log(chars=4000) -> str:
    try:
        with open(_COMFYUI_LOG) as f:
            return f.read()[-chars:]
    except Exception:
        return "(log unavailable)"

def _wait_for_comfyui(timeout=600):
    for i in range(timeout):
        # Fail fast: if ComfyUI process died, no point waiting
        if _proc.poll() is not None:
            print(f"[BOOT] ComfyUI process exited (code {_proc.poll()}) after {i}s. Log tail:\n{_tail_comfyui_log()}")
            return False
        try:
            urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=2)
            return True
        except Exception:
            if i > 0 and i % 60 == 0:
                print(f"[BOOT] Still waiting for ComfyUI... {i}s elapsed. Log tail:\n{_tail_comfyui_log(1500)}")
            time.sleep(1)
    print(f"[BOOT] ComfyUI startup timeout ({timeout}s). Log tail:\n{_tail_comfyui_log()}")
    return False

def _submit_prompt(workflow: dict) -> str:
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["prompt_id"]

def _poll_history(prompt_id: str, timeout=3600) -> dict:
    for _ in range(timeout // 2):
        try:
            with urllib.request.urlopen(
                f"{COMFYUI_URL}/history/{prompt_id}", timeout=10
            ) as resp:
                history = json.loads(resp.read())
            if prompt_id in history:
                return history[prompt_id]
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Job {prompt_id} timed out after {timeout}s")

def _fetch_view(filename: str, subfolder: str, ftype: str) -> bytes:
    url = f"{COMFYUI_URL}/view?filename={filename}&subfolder={subfolder}&type={ftype}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()

def _upload_s3(key: str, data: bytes) -> str:
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    _s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=data, ContentType=content_type)
    if BUCKET_PUBLIC_URL:
        return f"{BUCKET_PUBLIC_URL}/{key}"
    return f"{BUCKET_ENDPOINT_URL}/{BUCKET_NAME}/{key}"

def _collect_outputs(history: dict, job_id: str) -> list:
    """
    Raccoglie immagini e video dall'output ComfyUI.
    VHS_VideoCombine scrive i video (mp4/webm) sotto la chiave 'gifs' → la includiamo.
    Ogni output → upload S3 (url) oppure base64 (fallback test).
    """
    results = []
    idx = 0
    for node_output in history.get("outputs", {}).values():
        for media_key in ("images", "videos", "gifs"):
            for f in node_output.get(media_key, []):
                fname = f["filename"]
                subfolder = f.get("subfolder", "")
                ftype = f.get("type", "output")
                kind = "video" if media_key in ("videos", "gifs") else "image"
                try:
                    binary = _fetch_view(fname, subfolder, ftype)
                except Exception as e:
                    print(f"[WARN] fetch {fname} failed: {e}")
                    continue

                if _s3:
                    ext = os.path.splitext(fname)[1] or (".mp4" if kind == "video" else ".png")
                    key = f"studio/{job_id}/output_{idx}{ext}"
                    try:
                        url = _upload_s3(key, binary)
                        results.append({"filename": fname, "type": kind, "url": url})
                    except Exception as e:
                        print(f"[WARN] S3 upload {fname} failed: {e}")
                else:
                    # Fallback base64 — solo test, NON usare per video in produzione
                    results.append({
                        "filename": fname,
                        "type": kind,
                        "data": base64.b64encode(binary).decode("utf-8"),
                    })
                idx += 1
    return results

def _save_input_images(images: list) -> list:
    os.makedirs(INPUT_DIR, exist_ok=True)
    saved = []
    for img in images:
        name = img.get("name", f"input_{uuid.uuid4().hex[:8]}.png")
        # accetta sia "data" che "image" (convenzione worker-comfyui ufficiale)
        b64 = img.get("data") or img.get("image")
        path = os.path.join(INPUT_DIR, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        saved.append(name)
    return saved

# ── Avvio ComfyUI al boot del container ──────────────────────────────────────

print(f"[BOOT] Python: {sys.executable} {sys.version}")
print(f"[BOOT] Starting ComfyUI...")
try:
    _proc = _start_comfyui()
except Exception as e:
    print(f"[BOOT] FATAL: failed to launch ComfyUI process: {e}", flush=True)
    raise

if not _wait_for_comfyui():
    raise RuntimeError("ComfyUI failed to start within 10 minutes")
print("[BOOT] ComfyUI ready.")

# ── Handler ───────────────────────────────────────────────────────────────────

def handler(job):
    job_input = job.get("input", {})
    workflow = job_input.get("workflow")
    job_id = job.get("id", uuid.uuid4().hex[:12])

    if not workflow:
        return {"error": "Missing 'workflow' in input (must be ComfyUI API format)"}

    if "images" in job_input:
        _save_input_images(job_input["images"])

    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        prompt_id = _submit_prompt(workflow)
        print(f"[JOB {job_id}] prompt_id={prompt_id}")
        history = _poll_history(prompt_id)
        outputs = _collect_outputs(history, job_id)
        print(f"[JOB {job_id}] Done — {len(outputs)} output(s)")
        return {"outputs": outputs, "prompt_id": prompt_id}
    except Exception as e:
        return {"error": str(e)}

runpod.serverless.start({"handler": handler})
