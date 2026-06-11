# DIAGNOSTIC: codice REALE — eseguito PRIMA di qualsiasi altro import
import sys as _sys, os as _os
print(f"[STARTUP] Python {_sys.version} | pid={_os.getpid()}", flush=True)

# CUDA DIAGNOSTIC: verifica SM 12.x compatibility prima di qualsiasi altro import
try:
    import torch as _torch
    print(f"[CUDA] torch={_torch.__version__} cuda_compiled={_torch.version.cuda}", flush=True)
    if _torch.cuda.is_available():
        _dev = _torch.cuda.get_device_name(0)
        _cap = _torch.cuda.get_device_capability(0)
        print(f"[CUDA] device={_dev} SM={_cap[0]}.{_cap[1]}", flush=True)
        # Stesso kernel test usato da start.sh — se crasha qui vediamo l'errore esatto
        _result = (_torch.zeros(8, device='cuda') + 1).sum().item()
        print(f"[CUDA] kernel test OK result={_result}", flush=True)
    else:
        print("[CUDA] torch.cuda.is_available() = False", flush=True)
except Exception as _cuda_e:
    print(f"[CUDA] ERROR: {_cuda_e}", flush=True)

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
import threading
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
    try:
        import boto3
        _s3 = boto3.client(
            "s3",
            endpoint_url=BUCKET_ENDPOINT_URL,
            aws_access_key_id=BUCKET_ACCESS_KEY_ID,
            aws_secret_access_key=BUCKET_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        print("[BOOT] S3 output storage configured.", flush=True)
    except ImportError:
        print("[BOOT] WARNING: boto3 not installed — S3 disabled, outputs will be base64.", flush=True)
    except Exception as e:
        print(f"[BOOT] WARNING: S3 init failed ({e}) — outputs will be base64.", flush=True)
else:
    print("[BOOT] No S3 configured — outputs will be base64 (test mode only).", flush=True)

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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[SUBMIT] ComfyUI /prompt error {e.code}: {body[:2000]}", flush=True)
        raise RuntimeError(f"ComfyUI /prompt HTTP {e.code}: {body[:1000]}")

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
    Se la history è vuota, scansiona direttamente il filesystem come fallback.
    """
    results = []
    idx = 0

    # ── Log diagnostico della struttura history ───────────────────────────────
    raw_outputs = history.get("outputs", {})
    print(f"[COLLECT] history keys: {list(history.keys())}", flush=True)
    print(f"[COLLECT] output nodes: {list(raw_outputs.keys())}", flush=True)
    for nid, nout in raw_outputs.items():
        print(f"[COLLECT]   node {nid}: keys={list(nout.keys())}", flush=True)
        for mk in ("images", "videos", "gifs", "animated"):
            if nout.get(mk):
                print(f"[COLLECT]     {mk}: {nout[mk]}", flush=True)

    # ── Raccolta da history ComfyUI ───────────────────────────────────────────
    for node_output in raw_outputs.values():
        for media_key in ("images", "videos", "gifs", "animated"):
            for f in node_output.get(media_key, []):
                fname = f["filename"] if isinstance(f, dict) else f
                subfolder = f.get("subfolder", "") if isinstance(f, dict) else ""
                ftype = f.get("type", "output") if isinstance(f, dict) else "output"
                kind = "video" if media_key in ("videos", "gifs", "animated") else "image"
                print(f"[COLLECT] fetching {fname} subfolder={subfolder!r} type={ftype}", flush=True)
                try:
                    binary = _fetch_view(fname, subfolder, ftype)
                    print(f"[COLLECT] fetched {fname}: {len(binary)} bytes", flush=True)
                except Exception as e:
                    print(f"[WARN] fetch {fname} failed: {e}", flush=True)
                    continue

                if _s3:
                    ext = os.path.splitext(fname)[1] or (".mp4" if kind == "video" else ".png")
                    key = f"studio/{job_id}/output_{idx}{ext}"
                    try:
                        url = _upload_s3(key, binary)
                        results.append({"filename": fname, "type": kind, "url": url})
                        print(f"[COLLECT] uploaded → {url}", flush=True)
                    except Exception as e:
                        print(f"[WARN] S3 upload {fname} failed: {e}", flush=True)
                else:
                    results.append({
                        "filename": fname,
                        "type": kind,
                        "data": base64.b64encode(binary).decode("utf-8"),
                    })
                    print(f"[COLLECT] encoded {fname} as base64", flush=True)
                idx += 1

    # ── Fallback: scansione filesystem se history non ha restituito nulla ─────
    if not results:
        print(f"[COLLECT] history vuota — scansione filesystem {OUTPUT_DIR}", flush=True)
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in (".mp4", ".webm", ".gif", ".png", ".jpg", ".jpeg"):
                    continue
                fpath = os.path.join(root, fname)
                kind = "video" if ext in (".mp4", ".webm", ".gif") else "image"
                print(f"[COLLECT] filesystem: {fpath} ({kind})", flush=True)
                try:
                    with open(fpath, "rb") as fh:
                        binary = fh.read()
                    print(f"[COLLECT] read {fname}: {len(binary)} bytes", flush=True)
                except Exception as e:
                    print(f"[WARN] read {fpath} failed: {e}", flush=True)
                    continue

                if _s3:
                    key = f"studio/{job_id}/output_{idx}{ext}"
                    try:
                        url = _upload_s3(key, binary)
                        results.append({"filename": fname, "type": kind, "url": url})
                        print(f"[COLLECT] fs→S3: {url}", flush=True)
                    except Exception as e:
                        print(f"[WARN] S3 upload {fname} failed: {e}", flush=True)
                else:
                    results.append({
                        "filename": fname,
                        "type": kind,
                        "data": base64.b64encode(binary).decode("utf-8"),
                    })
                    print(f"[COLLECT] fs→base64: {fname}", flush=True)
                idx += 1

    print(f"[COLLECT] total outputs: {len(results)}", flush=True)
    return results

def _save_input_images(images: list) -> list:
    """
    Salva immagini in INPUT_DIR. Accetta due forme per ogni entry:
      {"name": "photo.jpg", "url": "https://..."}   ← download da URL (Supabase Storage)
      {"name": "photo.jpg", "data": "<base64>"}      ← base64 inline (o "image" key)
    Restituisce lista di filename salvati.
    """
    os.makedirs(INPUT_DIR, exist_ok=True)
    saved = []
    for img in images:
        name = img.get("name", f"input_{uuid.uuid4().hex[:8]}.png")
        path = os.path.join(INPUT_DIR, name)
        url = img.get("url")
        if url:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            with open(path, "wb") as f:
                f.write(data)
        else:
            # accetta sia "data" che "image" (convenzione worker-comfyui ufficiale)
            b64 = img.get("data") or img.get("image")
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64))
        saved.append(name)
    return saved

# ── Avvio ComfyUI in background — non blocca RunPod health check ─────────────
# runpod.serverless.start() viene chiamato subito; ComfyUI carica in parallelo.
# Senza questo, RunPod's container startup timeout uccide il worker prima che
# ComfyUI sia pronto (5-10 min), causando throttle immediato.

_comfyui_ready = threading.Event()
_comfyui_error = None
_proc = None

def _boot_comfyui():
    global _proc, _comfyui_error
    print(f"[BOOT] Python: {sys.executable} {sys.version}", flush=True)

    # Se start.sh ha già avviato ComfyUI (immagine base NGC), lo rileva e non rilancia.
    # Aspetta fino a 60s che start.sh finisca di avviarlo prima di rinunciare e partire noi.
    print("[BOOT] Checking if ComfyUI already running (start.sh may have launched it)...", flush=True)
    for i in range(60):
        try:
            urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=2)
            print(f"[BOOT] ComfyUI already up after {i}s — skipping self-launch.", flush=True)
            _comfyui_ready.set()
            return
        except Exception:
            time.sleep(1)

    # ComfyUI non è partito da start.sh: lo avviamo noi (CMD override scenario).
    print("[BOOT] ComfyUI not detected — launching self...", flush=True)
    try:
        _proc = _start_comfyui()
    except Exception as e:
        _comfyui_error = f"Failed to launch ComfyUI: {e}"
        print(f"[BOOT] FATAL: {_comfyui_error}", flush=True)
        _comfyui_ready.set()
        return
    if _wait_for_comfyui():
        print("[BOOT] ComfyUI ready.", flush=True)
    else:
        _comfyui_error = "ComfyUI failed to start within 10 minutes"
        print(f"[BOOT] ERROR: {_comfyui_error}", flush=True)
    _comfyui_ready.set()

threading.Thread(target=_boot_comfyui, daemon=True).start()
print("[BOOT] ComfyUI boot started in background — registering RunPod handler.", flush=True)

# ── Handler ───────────────────────────────────────────────────────────────────

def _run_diagnostics() -> dict:
    """
    Diagnostic mode: lista file modelli sul volume + nodi ComfyUI installati.
    Invocato quando il job ha {"diagnostic": true} nell'input.
    Utile per verificare se i modelli WAN 2.2 sono sul volume e se WanVideoWrapper è installato.
    """
    diag = {}

    # 1. Filesystem: directory modelli su /runpod-volume
    VOLUME = "/runpod-volume"
    model_dirs = [
        "models/diffusion_models",
        "models/text_encoders",
        "models/vae",
        "models/loras",
        "models/clip_vision",
    ]
    fs_results = {}
    for mdir in model_dirs:
        path = os.path.join(VOLUME, mdir)
        try:
            files = []
            for root, dirs, fnames in os.walk(path):
                for fn in fnames:
                    fp = os.path.join(root, fn)
                    try:
                        size_mb = os.path.getsize(fp) / 1024 / 1024
                        files.append(f"{fn} ({size_mb:.1f} MB)")
                    except Exception:
                        files.append(fn)
            fs_results[mdir] = files if files else ["(empty)"]
        except Exception as e:
            fs_results[mdir] = [f"ERROR: {e}"]
    diag["volume_files"] = fs_results

    # 2. ComfyUI /object_info — lista tutti i nodi installati
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/object_info", timeout=10) as resp:
            node_info = json.loads(resp.read())
        node_names = sorted(node_info.keys())
        wan_nodes = [n for n in node_names if "wan" in n.lower() or "Wan" in n]
        kjnodes = [n for n in node_names if "KJ" in n or "kj" in n.lower()]
        vhs_nodes = [n for n in node_names if "VHS" in n or "VideoHelper" in n]
        diag["comfyui_nodes_total"] = len(node_names)
        diag["wan_nodes"] = wan_nodes
        diag["kjnodes"] = kjnodes[:20]  # primi 20
        diag["vhs_nodes"] = vhs_nodes[:20]
    except Exception as e:
        diag["comfyui_nodes_error"] = str(e)

    # 3. ComfyUI /system_stats
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=10) as resp:
            diag["system_stats"] = json.loads(resp.read())
    except Exception as e:
        diag["system_stats_error"] = str(e)

    print(f"[DIAG] volume_files: {json.dumps(fs_results, indent=2)}", flush=True)
    print(f"[DIAG] wan_nodes: {diag.get('wan_nodes')}", flush=True)
    return diag


def handler(job):
    # Aspetta ComfyUI se ancora in avvio (background thread)
    if not _comfyui_ready.wait(timeout=600):
        return {"error": "ComfyUI startup timeout (600s)"}
    if _comfyui_error:
        return {"error": f"ComfyUI boot failed: {_comfyui_error}"}

    job_input = job.get("input", {})
    workflow = job_input.get("workflow")
    job_id = job.get("id", uuid.uuid4().hex[:12])

    # ── Diagnostic mode ────────────────────────────────────────────────────────
    if job_input.get("diagnostic"):
        print(f"[JOB {job_id}] Diagnostic mode requested", flush=True)
        return _run_diagnostics()

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
