# RunPod official Blackwell fix — NGC base con supporto nativo SM 12.x.
# fix-dr-1170: bug aperto su startup crash + variante NGC per RTX PRO 6000 SM 12.x.
# PyTorch wheel standard (cu118/cu126) mancano dei cubins SM 12.0/12.2 → crash silenzioso.
# L'immagine NGC include CUDA nativo compilato per SM 12.x da NVIDIA direttamente.
FROM runpod/worker-comfyui:fix-dr-1170-ngc-base-blackwell

ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume
# Disabilita dynamo — sicurezza aggiuntiva su Blackwell (non interferisce con NGC)
ENV TORCHDYNAMO_DISABLE=1
ENV TORCH_COMPILE_DISABLE=1

# ── Custom nodes WAN 2.2 I2V ─────────────────────────────────────────────────
COPY install_nodes.sh /install_nodes.sh
RUN chmod +x /install_nodes.sh && /install_nodes.sh

# ── Model paths → Network Volume EUR-IS-1 ────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# ── Handler custom (background thread + S3/R2 output) ────────────────────────
COPY handler.py /handler.py

CMD ["python", "-u", "/handler.py"]
