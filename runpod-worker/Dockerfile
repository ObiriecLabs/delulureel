# nvidia/cuda:12.6.3 + Ubuntu 22.04 + Python 3.11 STABILE da deadsnakes PPA.
# Ubuntu 22.04 main repo ha python3.11=3.11.0rc1 (broken) — deadsnakes dà 3.11.9+.
# PyTorch 2.6+ con cu126: unico release con supporto nativo Blackwell SM 10.x.
# pytorch/pytorch base (2.5.1, 2.6.0) crashava silenziosamente — causa conda env.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume
# Disabilita TorchDynamo — causa crash silenzioso su Blackwell SM 10.x
ENV TORCHDYNAMO_DISABLE=1
ENV TORCH_COMPILE_DISABLE=1

# ── Python 3.11 stabile (deadsnakes) + system deps ───────────────────────────
RUN apt-get update && apt-get install -y software-properties-common curl && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y \
        python3.11 python3.11-dev python3.11-distutils \
        git wget \
        libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── pip + alias ───────────────────────────────────────────────────────────────
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 && \
    update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# ── PyTorch 2.6 + CUDA 12.6 (SM 10.x Blackwell nativo) ──────────────────────
RUN pip install --no-cache-dir \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu126

# ── ComfyUI (escludi torch da requirements per non sovrascrivere con CPU build)
WORKDIR /comfyui
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git . && \
    grep -vE '^torch' requirements.txt > /tmp/comfyui_reqs.txt && \
    pip install --no-cache-dir -r /tmp/comfyui_reqs.txt

# ── RunPod SDK + S3 ──────────────────────────────────────────────────────────
RUN pip install --no-cache-dir runpod requests boto3

# ── Custom nodes ─────────────────────────────────────────────────────────────
COPY install_nodes.sh /install_nodes.sh
RUN chmod +x /install_nodes.sh && /install_nodes.sh

# ── Config + Handler ─────────────────────────────────────────────────────────
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY handler.py /handler.py

EXPOSE 8188
CMD ["python", "-u", "/handler.py"]
