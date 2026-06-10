# RunPod official Blackwell fix — NGC base con supporto nativo SM 12.x.
# fix-dr-1170: bug aperto su startup crash + variante NGC per RTX PRO 6000 SM 12.x.
# PyTorch wheel standard (cu118/cu126) mancano dei cubins SM 12.0/12.2 → crash silenzioso.
# Tag CORRETTO: fix-dr-1170-ngc-base-blackwell-BASE-cuda12.8.1 (13.8 GB, pushato 2026-06-10).
# NB: "-base-" in mezzo al tag (non "-base" finale) — "-base" finale indica variante ComfyUI.
# NGC26.05 variant (20.6 GB) omessa: cuda12.8.1 è sufficiente e 7 GB più leggera.
FROM runpod/worker-comfyui:fix-dr-1170-ngc-base-blackwell-base-cuda12.8.1

ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume
# Disabilita dynamo — sicurezza aggiuntiva su Blackwell (non interferisce con NGC)
ENV TORCHDYNAMO_DISABLE=1
ENV TORCH_COMPILE_DISABLE=1

# ── Dipendenze Python garantite (boto3 potrebbe mancare nell'immagine NGC) ───
RUN /opt/venv/bin/pip install --no-cache-dir boto3

# ── Custom nodes WAN 2.2 I2V ─────────────────────────────────────────────────
COPY install_nodes.sh /install_nodes.sh
RUN chmod +x /install_nodes.sh && /install_nodes.sh

# ── Model paths → Network Volume EUR-IS-1 ────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# ── Handler custom (background thread + S3/R2 output) ────────────────────────
# Sovrascrive il handler.py originale dell'immagine base — start.sh lo chiamerà.
# NON fare override di CMD: lasciamo che start.sh gestisca GPU validation +
# avvio ComfyUI + invocazione handler. Handler.py rileva se ComfyUI è già up.
COPY handler.py /handler.py
