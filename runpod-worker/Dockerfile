FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/comfyui
ENV RUNPOD_VOLUME=/runpod-volume
# Disabilita torch.compile / dynamo — evita il crash di comfy_kitchen
# su Python Ubuntu 22.04 (comfy/quant_ops.py → torch._dynamo/polyfills/sys.py →
# AttributeError: module 'sys' has no attribute 'get_int_max_str_digits')
ENV TORCHDYNAMO_DISABLE=1
ENV TORCH_COMPILE_DISABLE=1

# ── System deps ──────────────────────────────────────────────────────────────
# NOTA: python3.11 dall'apt Ubuntu 22.04 è 3.11.0rc1 (pre-release) — manca
# sys.get_int_max_str_digits → crash di torch._dynamo / comfy_kitchen.
# Usiamo python3 = 3.10.12 di Ubuntu 22.04 che ha get_int_max_str_digits (da 3.10.7+).
RUN apt-get update && apt-get install -y \
    python3 python3-venv python3-dev python3-pip \
    git wget curl \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── ComfyUI ───────────────────────────────────────────────────────────────────
WORKDIR /comfyui
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git . && \
    pip3 install --no-cache-dir torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu126 && \
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
