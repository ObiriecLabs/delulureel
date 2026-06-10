# PyTorch 2.6.0 + CUDA 12.6: primo release con supporto nativo Blackwell SM 10.x.
# pytorch/pytorch:2.5.1 crashava su RTX PRO 6000 Blackwell (EUR-IS-1) — SM 10.x
# non supportato in 2.5.x (kernel PTX mancanti → crash silenzioso all'import).
FROM pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume

# ── System deps ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    git wget curl \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── ComfyUI (torch già incluso nel base image) ───────────────────────────────
WORKDIR /comfyui
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git . && \
    pip install --no-cache-dir -r requirements.txt

# ── RunPod SDK + S3 (output object storage) ──────────────────────────────────
RUN pip install --no-cache-dir runpod requests boto3

# ── Custom nodes Obiriec ─────────────────────────────────────────────────────
COPY install_nodes.sh /install_nodes.sh
RUN chmod +x /install_nodes.sh && /install_nodes.sh

# ── Model paths → RunPod Network Volume ──────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# ── Handler ───────────────────────────────────────────────────────────────────
COPY handler.py /handler.py

EXPOSE 8188

CMD ["python", "-u", "/handler.py"]
