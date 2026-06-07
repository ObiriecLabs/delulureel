# DELULUREEL Studio — Manifest Modelli Cloud (RunPod Network Volume)

Derivato dall'analisi di **116 workflow** Obiriec (159 modelli canonici estratti).
Curation per **famiglia di generazione offerta**. Tre livelli:

- **CORE** → va sul Network Volume cloud (produzione clienti + tua produzione)
- **OPTIONAL** → aggiungibile dopo, secondo necessità
- **LOCAL_ONLY** → resta sul tuo 6TB locale (NSFW, esperimenti, superati). MAI sul cloud a pagamento.

Subfolder = struttura `ComfyUI/models/` (montata su `/runpod-volume/models/`).

---

## CORE — set di produzione cloud (~190 GB)

### 🎬 VIDEO — LTX 2.3 (flagship) — ~34 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| ltx-2.3-22b-dev-fp8.safetensors | diffusion_models | 22 | Lightricks/LTX-2.3-fp8 |
| LTX23_video_vae_bf16.safetensors | vae | 1.5 | Kijai/LTX2.3_comfy |
| LTX23_audio_vae_bf16.safetensors | vae | 0.5 | Kijai/LTX2.3_comfy |
| ltx-2.3_text_projection_bf16.safetensors | text_encoders | 0.2 | Kijai/LTX2.3_comfy |
| taeltx2_3.safetensors | vae_approx | 0.1 | Kijai/LTX2.3_comfy |
| gemma_3_12B_it_fp4_mixed.safetensors | text_encoders | 7.0 | Comfy-Org/ltx-2 |
| ltx-2.3-spatial-upscaler-x2-1.1.safetensors | upscale_models | 1.0 | Lightricks/LTX-2.3 |
| ltx-2.3-22b-distilled-lora-384.safetensors | loras | 0.4 | Lightricks/LTX-2.3 |
| ltx_2.3_22b_distilled_1.1_lora_dynamic_...rank_111_bf16.safetensors | loras | 0.5 | Comfy-Org/ltx-2.3 |
| ltx-av-step-1751000_vocoder_24K.safetensors | (audio) | 0.1 | Kijai/LTX2.3_comfy |

### 🎬 VIDEO — Wan 2.2 (workhorse T2V+I2V) — ~62 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors | diffusion_models | 14 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors | diffusion_models | 14 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors | diffusion_models | 14 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors | diffusion_models | 14 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan_2.1_vae.safetensors | vae | 0.3 | Comfy-Org/Wan_2.1_ComfyUI_repackaged |
| wan2.2_vae.safetensors | vae | 0.5 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| umt5_xxl_fp8_e4m3fn_scaled.safetensors | text_encoders | 6.7 | Comfy-Org/Wan_2.1_ComfyUI_repackaged |
| wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors | loras | 0.6 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors | loras | 0.6 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors | loras | 0.6 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors | loras | 0.6 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |

### 🗣️ AVATAR / TALKING-HEAD — InfiniteTalk (Wan 2.1 base) — ~26 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| Wan2_1-I2V-14B-720P_fp8_e4m3fn.safetensors | diffusion_models | 17 | Kijai/WanVideo_comfy |
| Wan2_1-InfiniteTalk_Single_Q8.gguf | diffusion_models | 8 | Kijai/WanVideo_comfy (gguf) |
| wav2vec2-chinese-base_fp16.safetensors | (audio enc) | 0.4 | TencentGameMate/Kijai |
| clip_vision_h.safetensors | clip_vision | 1.2 | Comfy-Org/Wan_2.1_ComfyUI_repackaged |

### 🖼️ IMAGE — Flux — ~21 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| flux1-krea-dev_fp8_scaled.safetensors | diffusion_models | 11 | Comfy-Org/FLUX.1-Krea-dev_ComfyUI |
| t5xxl_fp16.safetensors | text_encoders | 9.5 | comfyanonymous/flux_text_encoders |
| clip_l.safetensors | text_encoders | 0.25 | comfyanonymous/flux_text_encoders |
| ae.safetensors (flux VAE) | vae | 0.3 | black-forest-labs/FLUX.1-dev |

### 🖼️ IMAGE — SDXL (4 checkpoint, copertura completa) — ~28 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| juggernautXL_ragnarokBy.safetensors | checkpoints | 7.1 | CivitAI 133005 → **LOCAL upload** |
| realvisxlV50_v50LightningBakedvae.safetensors | checkpoints | 6.5 | CivitAI RealVisXL V5.0 → **LOCAL upload** |
| CyberRealisticXLPlay_V10.0_FP16.safetensors | checkpoints | 6.5 | CivitAI → **LOCAL upload** |
| ponyDiffusionV6XL_v6StartWithThisOne.safetensors | checkpoints | 6.5 | CivitAI 257749 → **LOCAL upload** |
| clip_g.safetensors (SDXL text enc) | text_encoders | 1.4 | Comfy-Org |
| dmd2_sdxl_4step_lora.safetensors | loras | 0.4 | tianweiy/DMD2 |

### 🎚️ SUPPORT condivisi (upscale, detect, controlnet, ipadapter) — ~9 GB
| File | Subfolder | ~GB | Fonte |
|---|---|---|---|
| 4x-UltraSharp.pth | upscale_models | 0.07 | Kim2091 (usato in 46 wf!) |
| RealESRGAN_x4plus.safetensors | upscale_models | 0.07 | Comfy-Org/Real-ESRGAN_repackaged |
| controlnet-openpose-sdxl-1.0.safetensors | controlnet | 2.5 | xinsir/controlnet-openpose-sdxl-1.0 |
| ip-adapter-plus-face_sdxl_vit-h.safetensors | ipadapter | 0.85 | h94/IP-Adapter |
| face_yolov8m.pt | ultralytics/bbox | 0.05 | Bingsu/adetailer |
| hand_yolov8s.pt | ultralytics/bbox | 0.05 | Bingsu/adetailer |
| sam_vit_b_01ec64.pth | sams | 0.4 | facebook/SAM |
| rife49.pth | (frame interp) | 0.06 | Fannovel16 RIFE |
| vae-ft-mse-840000.safetensors | vae | 0.3 | stabilityai (SD1.5 vae) |
| clip_vision_vit_h.safetensors | clip_vision | 1.2 | h94 / Comfy-Org |

**TOTALE CORE ≈ 190 GB**  (di cui ~28 GB SDXL da upload locale, ~162 GB da HuggingFace)

---

## OPTIONAL — aggiungibili su richiesta (~80 GB se tutti)

| File | Famiglia | ~GB | Note |
|---|---|---|---|
| flux1-dev-fp8.safetensors | image_flux | 11 | base Flux dev (krea già copre il fotografico) |
| flux1-dev.safetensors | image_flux | 23 | versione full bf16 (pesante) |
| sd3.5_large_fp8_scaled.safetensors | image_sd35 | 8 | SD 3.5 Large |
| qwen_image_edit_2509_fp8_e4m3fn + vae + qwen_2.5_vl + lora | image_qwen | ~22 | image editing Qwen |
| hunyuan_video_i2v_720p_bf16.safetensors + vae | video_hunyuan | ~26 | I2V Hunyuan (LTX/Wan già coprono) |
| hunyuan-video-i2v-720p-Q4_K_M.gguf | video_hunyuan | 7 | versione leggera gguf |
| seedvr2_ema_7b-Q4_K_M.gguf | upscale | 5 | upscaler video avanzato |
| ltx-2.3-22b-ic-lora-lipdub-0.9 / hdr-0.9 | video_ltx | ~1 | IC-LoRA controllo LTX (lip-sync, HDR) |
| LongCat-Avatar-single_fp8 + distill lora | avatar | ~14 | avatar alternativo a InfiniteTalk |
| wan2.2_ti2v_5B_fp16.safetensors | video_wan | 10 | Wan 2.2 5B (più leggero) |
| flux2_dev_fp8mixed + mistral encoder | image_flux | ~20 | Flux 2 (molto pesante) |

---

## LOCAL_ONLY — restano sul tuo 6TB, NON sul cloud

**NSFW / adulti** (decisione separata — vedi nota):
Chroma_Deepthroat_v1_SeaEng, CHROMA_fp8, NSFW_Unlock_v2, Detailed_Pussy_Anatomy_v1,
penis LoRa 2.3, ltxdeepthroat_v01, ltx2310eros_v1, biglust17_v17, biglust_v17,
LTX2.3_VBVR_Reasoning_I2V_V2.

**Superati / vecchi**:
ltx-video-2b-v0.9, ltx-video-2b-v0.9.5, ltx-2-19b-* (LTX 2 vecchio), v1-5-pruned-emaonly (SD1.5),
mm_sd15_v3 (AnimateDiff SD1.5), wan2.1_t2v_1.3B, Wan2_1-T2V-14B (superato da 2.2 T2V),
CyberRealistic_V4.2 (SD1.5), epicrealism_naturalSin (SD1.5).

**Esperimenti / one-off** (count 1, fonte oscura):
model.safetensors, TCDecoder.ckpt, LQ_proj_in_v1.1.ckpt, posi_prompt.pth,
diffusion_pytorch_model_streaming_dmd, sulphur_lora_rank_768, MelBandRoformer_fp32,
yolox_l (torchscript/onnx), video_depth_anything_vits, Wan2.2-Animate-14B-Q3,
Wan2.1-14B-BindWeave-Q3, gemma GGUF varianti (usiamo i safetensors).

---

## Strategia download (vedi script)

1. **HuggingFace (~162 GB CORE)** → `download_models.sh` dentro un Pod RunPod CPU-only (rete datacenter, veloce). NON dal Mac.
2. **CivitAI/locali (~28 GB SDXL)** → `upload_local_models.sh`: li hai già sul 6TB, li spingi sul volume via `runpodctl` o S3 API. Evita il rebuild da CivitAI.
