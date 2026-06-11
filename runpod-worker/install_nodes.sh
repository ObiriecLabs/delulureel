#!/bin/bash
set -e
cd /comfyui/custom_nodes

# Usa il pip del venv ComfyUI, non il pip di sistema
PIP=/opt/venv/bin/pip3

echo "▶ Installing WAN 2.2 I2V nodes (minimal set)..."

git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git   || true
git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git           || true
git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git || true

echo "▶ Installing node requirements..."
for dir in /comfyui/custom_nodes/*/; do
    if [ -f "${dir}requirements.txt" ]; then
        echo "  → ${dir}"
        # --no-deps prima passata: evita che WanVideoWrapper scarichi PyTorch e
        # rompa i cubins NGC. Seconda passata con deps per tutto il resto (einops, ecc.)
        $PIP install --no-cache-dir --no-deps -r "${dir}requirements.txt" || true
        $PIP install --no-cache-dir -r "${dir}requirements.txt" || true
    fi
done

echo "✅ Custom nodes installed."
