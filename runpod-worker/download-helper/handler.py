"""
Download-helper handler — scarica i modelli CORE sul network volume.
Da usare SOLO su un endpoint temporaneo per popolare i volumi.
Dopo il download, eliminare l'endpoint e usare il worker ComfyUI principale.
"""
import runpod
import subprocess
import os
import sys

SCRIPT_PATH = "/download_models.sh"
VOL = "/runpod-volume"

def handler(job):
    job_input = job.get("input", {})
    hf_token = job_input.get("hf_token", os.environ.get("HF_TOKEN", ""))

    print(f"[DOWNLOAD] Avvio download su {VOL}", flush=True)
    print(f"[DOWNLOAD] HF_TOKEN presente: {bool(hf_token)}", flush=True)

    env = {**os.environ, "HF_TOKEN": hf_token, "VOL": VOL}

    # Mostra cosa c'è già sul volume prima di scaricare
    try:
        existing = subprocess.check_output(
            ["find", f"{VOL}/models", "-name", "*.safetensors", "-o", "-name", "*.pt", "-o", "-name", "*.gguf"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        existing_count = len(existing.splitlines()) if existing else 0
        print(f"[DOWNLOAD] File esistenti sul volume: {existing_count}", flush=True)
    except Exception:
        existing_count = 0
        print("[DOWNLOAD] Volume vuoto o models/ non esiste ancora", flush=True)

    proc = subprocess.Popen(
        ["bash", SCRIPT_PATH],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    returncode = proc.wait(timeout=14400)  # 4h max

    print(f"[DOWNLOAD] Script terminato con exit code: {returncode}", flush=True)

    # Lista file scaricati
    try:
        downloaded = subprocess.check_output(
            ["find", f"{VOL}/models", "-name", "*.safetensors", "-o", "-name", "*.pt", "-o", "-name", "*.gguf", "-o", "-name", "*.pth"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        files = downloaded.splitlines() if downloaded else []
    except Exception:
        files = []

    return {
        "status": "done" if returncode == 0 else "partial",
        "exit_code": returncode,
        "files_downloaded": len(files) - existing_count,
        "total_files_on_volume": len(files),
        "file_list": files[:50],  # prime 50 per non sforare il limite risposta
    }

runpod.serverless.start({"handler": handler})
