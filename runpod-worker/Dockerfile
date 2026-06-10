FROM nvidia/cuda:12.8.0-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume

# ── System deps ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    git wget curl \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

# ── ComfyUI ───────────────────────────────────────────────────────────────────
WORKDIR /comfyui
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git . && \
    pip3 install --no-cache-dir torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128 && \
    pip3 install --no-cache-dir -r requirements.txt

# ── RunPod SDK + S3 (output object storage) ──────────────────────────────────
RUN pip3 install --no-cache-dir runpod requests boto3

# ── Custom nodes Obiriec ─────────────────────────────────────────────────────
COPY install_nodes.sh /install_nodes.sh
RUN chmod +x /install_nodes.sh && /install_nodes.sh

# ── Model paths → RunPod Network Volume ──────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# ── Handler ───────────────────────────────────────────────────────────────────
COPY handler.py /handler.py

# Porta ComfyUI interna (non esposta all'esterno)
EXPOSE 8188

CMD ["python3", "-u", "/handler.py"]
