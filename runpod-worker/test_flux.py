#!/usr/bin/env python3
"""
Test Flux txt2img su RunPod endpoint a007azjm8d8r4k.
Usa il workflow testato localmente in ComfyUI (workflows/flux_txt2img.json).
"""
import json
import os
import sys
import time
import base64
import urllib.request
from pathlib import Path

RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")
ENDPOINT_ID    = "a007azjm8d8r4k"
BASE_URL       = f"https://api.runpod.io/v2/{ENDPOINT_ID}"
OUTPUT_DIR     = Path("/Volumes/ComfyUI_6TB/ComfyUI/output/DELULUREEL")

if not RUNPOD_API_KEY:
    sys.exit("RUNPOD_API_KEY non impostato — export RUNPOD_API_KEY=rpa_...")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

workflow_path = Path(__file__).parent / "workflows" / "flux_txt2img.json"
workflow = json.loads(workflow_path.read_text())

def rp_post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RUNPOD_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def rp_get(path: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

print("→ Invio job Flux txt2img...")
resp = rp_post("/run", {"input": {"workflow": workflow}})
job_id = resp.get("id")
if not job_id:
    sys.exit(f"Nessun job_id nella risposta: {resp}")
print(f"  job_id = {job_id}")

print("→ Polling status (max 20 min)...")
deadline = time.time() + 1200
while time.time() < deadline:
    st = rp_get(f"/status/{job_id}")
    status = st.get("status", "?")
    print(f"  [{time.strftime('%H:%M:%S')}] status={status}", end="")

    if status == "COMPLETED":
        print()
        out = st.get("output", {})
        if out.get("error"):
            print(f"ERRORE ComfyUI: {out['error']}")
            print(f"Log ComfyUI:\n{out.get('comfyui_log_tail', '(nessun log)')}")
            sys.exit(1)
        outputs = out.get("outputs", [])
        print(f"  {len(outputs)} output(s) ricevuti")
        for i, item in enumerate(outputs):
            fname = item.get("filename", f"output_{i}.png")
            url = item.get("url")
            data_b64 = item.get("data")
            dest = OUTPUT_DIR / fname
            if url:
                print(f"  → download da S3: {url}")
                with urllib.request.urlopen(url, timeout=120) as r:
                    dest.write_bytes(r.read())
            elif data_b64:
                dest.write_bytes(base64.b64decode(data_b64))
            else:
                print(f"  ⚠ output {i}: né url né data")
                continue
            print(f"  ✅ salvato: {dest} ({dest.stat().st_size // 1024} KB)")
        break

    elif status == "FAILED":
        print()
        print(f"JOB FALLITO: {json.dumps(st, indent=2)}")
        sys.exit(1)

    elif status in ("IN_QUEUE", "IN_PROGRESS"):
        q = st.get("queuePosition")
        if q is not None:
            print(f" · coda pos {q}", end="")
        print()
        time.sleep(8)

    else:
        print(f" · status sconosciuto, riprovo tra 8s")
        time.sleep(8)
else:
    sys.exit("Timeout 20 min superato")
