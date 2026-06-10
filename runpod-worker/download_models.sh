#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# DELULUREEL Studio — Download modelli CORE da HuggingFace SUL Network Volume.
#
# DOVE ESEGUIRLO: dentro un Pod RunPod CPU-only (~$0.02/h) nello STESSO
# datacenter del Network Volume, col volume montato su /workspace.
# Il download viaggia HuggingFace → datacenter RunPod (NON dal tuo Mac).
#
# USO:
#   export HF_TOKEN=hf_xxx           # opzionale, per modelli gated/rate-limit
#   bash download_models.sh
#
# I file SDXL (CivitAI) NON sono qui: usa upload_local_models.sh dal Mac.
# ─────────────────────────────────────────────────────────────────────────────
set -e

VOL="${VOL:-/workspace}"
M="$VOL/models"

mkdir -p "$M"/{diffusion_models,vae,text_encoders,clip_vision,loras,checkpoints,upscale_models,controlnet,ipadapter,vae_approx,sams,ultralytics/bbox}

dl () {  # dl <repo> <file_in_repo> <dest_subfolder>
  local fname
  fname=$(basename "$2")
  echo "▶ $fname"
  wget -q --show-progress -c \
    --header="Authorization: Bearer ${HF_TOKEN}" \
    -O "$M/$3/$fname" \
    "https://huggingface.co/$1/resolve/main/$2"
}

echo "═══ VIDEO — LTX 2.3 ═══"
dl Lightricks/LTX-2.3-fp8 ltx-2.3-22b-dev-fp8.safetensors diffusion_models
dl Lightricks/LTX-2.3 ltx-2.3-spatial-upscaler-x2-1.1.safetensors upscale_models
dl Lightricks/LTX-2.3 ltx-2.3-22b-distilled-lora-384.safetensors loras
dl Comfy-Org/ltx-2.3 split_files/loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors loras
dl Comfy-Org/ltx-2 split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors text_encoders
# LTX 2.3 support (VERIFY filenames esatti nel repo Kijai/LTX2.3_comfy):
dl Kijai/LTX2.3_comfy LTX23_video_vae_bf16.safetensors vae || echo "  ⚠ verifica nome file video_vae"
dl Kijai/LTX2.3_comfy LTX23_audio_vae_bf16.safetensors vae || echo "  ⚠ verifica nome file audio_vae"
dl Kijai/LTX2.3_comfy ltx-2.3_text_projection_bf16.safetensors text_encoders || echo "  ⚠ verifica text_projection"
dl Kijai/LTX2.3_comfy taeltx2_3.safetensors vae_approx || echo "  ⚠ verifica taeltx"

echo "═══ VIDEO — Wan 2.2 ═══"
WAN22=Comfy-Org/Wan_2.2_ComfyUI_Repackaged
dl $WAN22 split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors diffusion_models
dl $WAN22 split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors diffusion_models
dl $WAN22 split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors diffusion_models
dl $WAN22 split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors diffusion_models
dl $WAN22 split_files/vae/wan2.2_vae.safetensors vae
dl $WAN22 split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors loras
dl $WAN22 split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors loras
dl $WAN22 split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors loras
dl $WAN22 split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors loras
WAN21=Comfy-Org/Wan_2.1_ComfyUI_repackaged
dl $WAN21 split_files/vae/wan_2.1_vae.safetensors vae
dl $WAN21 split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors text_encoders
dl $WAN21 split_files/clip_vision/clip_vision_h.safetensors clip_vision

echo "═══ AVATAR — InfiniteTalk (Wan 2.1) ═══"
dl Kijai/WanVideo_comfy Wan2_1-I2V-14B-720P_fp8_e4m3fn.safetensors diffusion_models
dl Kijai/WanVideo_comfy Wan2_1-InfiniteTalk_Single_Q8.gguf diffusion_models || echo "  ⚠ verifica path InfiniteTalk"

echo "═══ IMAGE — Flux ═══"
dl Comfy-Org/FLUX.1-Krea-dev_ComfyUI split_files/diffusion_models/flux1-krea-dev_fp8_scaled.safetensors diffusion_models
dl comfyanonymous/flux_text_encoders t5xxl_fp16.safetensors text_encoders
dl comfyanonymous/flux_text_encoders clip_l.safetensors text_encoders
dl black-forest-labs/FLUX.1-dev ae.safetensors vae || echo "  ⚠ FLUX.1-dev è gated: serve HF_TOKEN o usa mirror"

echo "═══ SUPPORT condivisi ═══"
dl Comfy-Org/Real-ESRGAN_repackaged RealESRGAN_x4plus.safetensors upscale_models
dl Kim2091/UltraSharp 4x-UltraSharp.pth upscale_models || echo "  ⚠ verifica repo UltraSharp"
dl xinsir/controlnet-openpose-sdxl-1.0 diffusion_pytorch_model.safetensors controlnet || echo "  ⚠ rinomina in controlnet-openpose-sdxl-1.0"
dl h94/IP-Adapter "sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors" ipadapter
dl Bingsu/adetailer face_yolov8m.pt ultralytics/bbox
dl Bingsu/adetailer hand_yolov8s.pt ultralytics/bbox
dl stabilityai/sd-vae-ft-mse-original vae-ft-mse-840000-ema-pruned.safetensors vae || echo "  ⚠ verifica nome vae-ft-mse"

echo ""
echo "✅ Download CORE HuggingFace completato."
echo "   Ora dal Mac: upload_local_models.sh per i 4 checkpoint SDXL (CivitAI)."
echo "   Spegni questo Pod — il volume conserva i dati."
