#!/bin/bash
set -e
cd /comfyui/custom_nodes

echo "▶ Installing Obiriec custom nodes..."

# ── Core video generation ────────────────────────────────────────────────────
git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git
git clone --depth=1 https://github.com/kijai/ComfyUI-LTXVideo.git
git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git
git clone --depth=1 https://github.com/kijai/ComfyUI-HunyuanVideoWrapper.git

# ── Video utilities ──────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
git clone --depth=1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git

# ── Image quality / upscale ──────────────────────────────────────────────────
git clone --depth=1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale.git
git clone --depth=1 https://github.com/city96/ComfyUI-GGUF.git

# ── IP-Adapter + face ────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/cubiq/ComfyUI_IPAdapter_plus.git
git clone --depth=1 https://github.com/cubiq/ComfyUI_FaceAnalysis.git

# ── Pipeline & logic nodes ───────────────────────────────────────────────────
git clone --depth=1 https://github.com/WASasquatch/ComfyUI-Logic.git
git clone --depth=1 https://github.com/M1kep/ComfyLiterals.git
git clone --depth=1 https://github.com/theUpsider/ComfyUI-Logic.git || true
git clone --depth=1 https://github.com/Trung0246/ComfyUI-PromptRelay.git

# ── Utility / quality of life ────────────────────────────────────────────────
git clone --depth=1 https://github.com/crystian/ComfyUI-Crystools.git
git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Impact-Pack.git
git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git
git clone --depth=1 https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git
git clone --depth=1 https://github.com/yolain/ComfyUI-Easy-Use.git

# ── AnimateDiff ──────────────────────────────────────────────────────────────
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git

# ── Install requirements for each node ───────────────────────────────────────
echo "▶ Installing node requirements..."
for dir in /comfyui/custom_nodes/*/; do
    if [ -f "${dir}requirements.txt" ]; then
        echo "  → ${dir}"
        pip3 install --no-cache-dir -r "${dir}requirements.txt" || true
    fi
done

echo "✅ Custom nodes installed."
