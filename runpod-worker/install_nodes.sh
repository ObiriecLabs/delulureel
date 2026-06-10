#!/bin/bash
set -e
cd /comfyui/custom_nodes

echo "▶ Installing Obiriec custom nodes..."

# Tutti i clone usano || true: se un repo è privato/rimosso/irraggiungibile
# il build continua. I nodi critici (WanVideoWrapper, VideoHelperSuite)
# sono su repo pubblici stabili di kijai/Kosinkadink — fallono solo per
# problemi di rete transitori, che si risolveranno al retry.

# ── Core video generation ────────────────────────────────────────────────────
git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git           || true
git clone --depth=1 https://github.com/kijai/ComfyUI-LTXVideo.git          || true
git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git   || true
git clone --depth=1 https://github.com/kijai/ComfyUI-HunyuanVideoWrapper.git || true

# ── Video utilities ──────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git || true
git clone --depth=1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git || true

# ── Image quality / upscale ──────────────────────────────────────────────────
git clone --depth=1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale.git  || true
git clone --depth=1 https://github.com/city96/ComfyUI-GGUF.git              || true

# ── IP-Adapter + face ────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/cubiq/ComfyUI_IPAdapter_plus.git     || true
git clone --depth=1 https://github.com/cubiq/ComfyUI_FaceAnalysis.git       || true

# ── Pipeline & logic nodes ───────────────────────────────────────────────────
git clone --depth=1 https://github.com/WASasquatch/ComfyUI-Logic.git        || true
git clone --depth=1 https://github.com/M1kep/ComfyLiterals.git              || true
git clone --depth=1 https://github.com/theUpsider/ComfyUI-Logic.git         || true
git clone --depth=1 https://github.com/Trung0246/ComfyUI-PromptRelay.git    || true

# ── Utility / quality of life ────────────────────────────────────────────────
git clone --depth=1 https://github.com/crystian/ComfyUI-Crystools.git       || true
git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Impact-Pack.git     || true
git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git    || true
git clone --depth=1 https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git || true
git clone --depth=1 https://github.com/yolain/ComfyUI-Easy-Use.git          || true

# ── AnimateDiff ──────────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git || true

# ── Install requirements for each node ───────────────────────────────────────
echo "▶ Installing node requirements..."
for dir in /comfyui/custom_nodes/*/; do
    if [ -f "${dir}requirements.txt" ]; then
        echo "  → ${dir}"
        pip3 install --no-cache-dir -r "${dir}requirements.txt" || true
    fi
done

echo "✅ Custom nodes installed."
