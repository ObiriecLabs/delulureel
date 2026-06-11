#!/usr/bin/env python3
"""
Test B -- Direct RunPod API end-to-end (WAN 2.2 I2V)
Due job paralleli sulla stessa immagine, prompt diversi.

Usage:
  cd /Volumes/Crucial X9/CLAUDE_Works/DELULUREEL
  RUNPOD_API_KEY=rp_... RUNPOD_ENDPOINT=a007azjm8d8r4k venv/bin/python test_runpod_b.py
"""
import os, sys, json, time, base64, copy, urllib.request
from dotenv import load_dotenv
load_dotenv()

API_KEY  = os.environ.get("RUNPOD_API_KEY", "")
ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "")
BASE_URL = f"https://api.runpod.ai/v2/{ENDPOINT}"

if not API_KEY or not ENDPOINT:
    print("❌  RUNPOD_API_KEY o RUNPOD_ENDPOINT mancanti"); sys.exit(1)


def _req(method, path, body=None):
    url = f"{BASE_URL}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _build_workflow(prompt, negative, seed):
    _WORKFLOW_PATH = os.path.join(os.path.dirname(__file__), "core/studio_templates/wan_i2v.json")
    with open(_WORKFLOW_PATH) as f:
        wf = json.load(f)
    wf.pop("_meta", None)  # documentazione — non è un nodo ComfyUI
    for node in wf.values():
        cls = node.get("class_type", "")
        if cls == "WanVideoTextEncode":
            node["inputs"]["positive_prompt"] = prompt
            node["inputs"]["negative_prompt"] = negative
        elif cls == "WanVideoImageToVideoEncode":
            node["inputs"]["width"]      = 480
            node["inputs"]["height"]     = 832
            node["inputs"]["num_frames"] = 81
        inp = node.get("inputs", {})
        if "seed" in inp and isinstance(inp["seed"], int):
            inp["seed"] = seed
        if "noise_seed" in inp and isinstance(inp["noise_seed"], int):
            inp["noise_seed"] = seed
    return wf


# ── Immagine: cyberpunk woman dal ComfyUI output ───────────────────────────────
IMAGE_PATH = "/Volumes/ComfyUI_6TB/ComfyUI/output/ComfyUI_00001_.png"
print(f"📂  Carico immagine: {os.path.basename(IMAGE_PATH)}")
with open(IMAGE_PATH, "rb") as fh:
    img_b64 = base64.b64encode(fh.read()).decode()
print(f"   {os.path.getsize(IMAGE_PATH)//1024} KB → base64 OK\n")

images = [{"name": "input_photo.png", "image": img_b64}]

# ── Definizione test cases ─────────────────────────────────────────────────────
TESTS = [
    {
        "label": "TEST-1 — prompt neutro",
        "prompt": (
            "A cyberpunk woman walking forward slowly, smooth natural motion, "
            "cinematic lighting, photorealistic"
        ),
        "negative": "blur, distortion, extra limbs, watermark, text, static",
        "seed": 42,
    },
    {
        "label": "TEST-2 — prompt stress (materiali + luci dinamiche)",
        "prompt": (
            "Cyberpunk woman in black latex bodysuit walks forward with a slow confident stride, "
            "shiny latex material catching and reflecting red and teal neon lights as she moves, "
            "specular highlights shifting across the suit with each step, "
            "white bob hair swaying naturally with momentum, "
            "thigh-high boots stepping on wet reflective asphalt, "
            "puddles on the ground mirroring the neon glow, "
            "steam rising from street grates in the foggy background, "
            "neon signs flickering behind her, cinematic depth of field, "
            "slow-motion, photorealistic, 8K, hyperdetailed"
        ),
        "negative": (
            "static pose, frozen, motion blur, distorted limbs, extra fingers, "
            "text visible, watermark, overexposed, flat lighting, cartoon, anime"
        ),
        "seed": 777,
    },
]

# ── Submit entrambi i job ──────────────────────────────────────────────────────
jobs = []
for t in TESTS:
    wf = _build_workflow(t["prompt"], t["negative"], t["seed"])
    print(f"🚀  Submit {t['label']}...")
    res = _req("POST", "run", {"input": {"workflow": wf, "images": images}})
    run_id = res["id"]
    jobs.append({"label": t["label"], "run_id": run_id, "done": False, "result": None})
    print(f"   run_id = {run_id}")

print(f"\n⏳  Polling ogni 30s (cold start ~10-15 min per il primo job)...\n")

# ── Polling parallelo ─────────────────────────────────────────────────────────
elapsed = 0
POLL    = 30
MAX     = 60 * 45   # 45 min max

while elapsed < MAX:
    time.sleep(POLL)
    elapsed += POLL

    all_done = True
    for j in jobs:
        if j["done"]:
            continue
        all_done = False
        st     = _req("GET", f"status/{j['run_id']}")
        status = st.get("status", "IN_QUEUE")
        delay  = st.get("delayTime", 0)
        exec_t = st.get("executionTime", 0)
        wid    = st.get("workerId", "?")
        print(f"   [{elapsed:4d}s] {j['label'][:30]} | {status} | delay={delay}ms exec={exec_t}ms worker={wid}")

        if status == "COMPLETED":
            j["done"] = True
            j["result"] = st
            outputs = st.get("output", {}).get("outputs", [])
            print(f"\n   ✅  {j['label']} COMPLETED — {len(outputs)} output(s)")
            for idx, out in enumerate(outputs):
                if out.get("url"):
                    print(f"      🎬  VIDEO URL: {out['url']}")
                elif out.get("data"):
                    # Salva localmente come fallback
                    ext = ".mp4" if out.get("type") == "video" else ".png"
                    out_path = f"/tmp/runpod_test_{j['run_id'][:8]}_{idx}{ext}"
                    with open(out_path, "wb") as fh:
                        import base64 as _b64
                        fh.write(_b64.b64decode(out["data"]))
                    kb = os.path.getsize(out_path) // 1024
                    print(f"      💾  Salvato localmente: {out_path} ({kb} KB)")
                    print(f"         open '{out_path}'")
            print()

        elif status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            j["done"] = True
            j["result"] = st
            print(f"\n   ❌  {j['label']} {status}")
            print(f"      {st.get('error', '(no error message)')}\n")

    if all_done:
        break

# ── Riepilogo finale ──────────────────────────────────────────────────────────
print("=" * 60)
print("RIEPILOGO FINALE")
print("=" * 60)
for j in jobs:
    st = j["result"]
    if not st:
        print(f"  {j['label']}: TIMEOUT (ancora in coda)")
        continue
    status  = st.get("status", "?")
    exec_t  = st.get("executionTime", 0)
    wid     = st.get("workerId", "?")
    outputs = st.get("output", {}).get("outputs", []) if status == "COMPLETED" else []
    icon = "✅" if status == "COMPLETED" else "❌"
    print(f"  {icon} {j['label']}")
    print(f"     status={status} exec={exec_t}ms worker={wid}")
    if outputs:
        for out in outputs:
            print(f"     → {out.get('url') or '(base64 ' + str(len(out.get('data',''))//1024) + ' KB)'}")
    elif status != "COMPLETED":
        print(f"     error: {st.get('error', '?')[:120]}")

sys.exit(0 if all(j["result"] and j["result"].get("status") == "COMPLETED" for j in jobs) else 1)
