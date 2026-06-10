#!/bin/bash
set -e
cd /comfyui/custom_nodes

echo "▶ Installing WAN 2.2 I2V nodes (minimal set)..."

# ── WAN 2.2 Image-to-Video — nodi strettamente necessari ─────────────────────
git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git   || true
git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git           || true
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git || true

# ── Install requirements ──────────────────────────────────────────────────────
echo "▶ Installing node requirements..."
for dir in /comfyui/custom_nodes/*/; do
    if [ -f "${dir}requirements.txt" ]; then
        echo "  → ${dir}"
        pip3 install --no-cache-dir -r "${dir}requirements.txt" || true
    fi
done

echo "✅ Custom nodes installed."
