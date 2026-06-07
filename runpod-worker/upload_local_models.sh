#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# DELULUREEL Studio — Upload checkpoint SDXL (fonte CivitAI/locale) sul Network
# Volume RunPod via S3-compatible API. Eseguire DAL MAC.
#
# Questi 4 checkpoint non hanno una fonte HuggingFace pulita: li hai già sul 6TB,
# li spingiamo sul volume una volta sola (~28 GB di upload).
#
# PREREQUISITI:
#   - aws CLI:  brew install awscli
#   - RunPod → Settings → S3 API Keys → Create  (salva access key user_*** e secret rps_***)
#   - aws configure  (Access Key = user_***, Secret = rps_***, region/output vuoti)
#   - VOLUME_ID e DATACENTER del tuo Network Volume
#
# USO:
#   bash upload_local_models.sh
#   (override opzionale: VOLUME_ID=xxx DC=eur-is-1 bash upload_local_models.sh)
# ─────────────────────────────────────────────────────────────────────────────
set -e

VOLUME_ID="${VOLUME_ID:-kv0z33c56q}"
DC="${DC:-eur-is-1}"

ENDPOINT="https://s3api-${DC}.runpod.io/"
SRC="/Volumes/ComfyUI_6TB/ComfyUI/models/checkpoints"
DEST="s3://${VOLUME_ID}/models/checkpoints"

put () {  # put <local_file> <remote_name>
  echo "▶ Upload $2 ..."
  aws s3 cp "$SRC/$1" "$DEST/$2" \
    --region "$DC" --endpoint-url "$ENDPOINT" \
    --cli-read-timeout 7200 --cli-connect-timeout 120
}

put "juggernautXL_ragnarokBy.safetensors"          "juggernautXL_ragnarokBy.safetensors"
put "realvisxlV50_v50LightningBakedvae.safetensors" "realvisxlV50_v50LightningBakedvae.safetensors"
put "ponyDiffusionV6XL_v6StartWithThisOne.safetensors" "ponyDiffusionV6XL_v6StartWithThisOne.safetensors"

# ⚠ ATTENZIONE CyberRealistic:
#   I workflow chiamano  CyberRealisticXLPlay_V10.0_FP16.safetensors
#   In locale hai SOLO   CyberRealisticXLPlay_V9.0_FP32.safetensors (13GB FP32)
#   Opzione A (consigliata): scarica V10 FP16 da CivitAI (più leggero, 6.5GB) e mettilo nel cloud
#   Opzione B: carica il V9 FP32 locale rinominandolo (più pesante, e versione vecchia)
# Decommentare la riga scelta:
# put "CyberRealisticXLPlay_V9.0_FP32.safetensors" "CyberRealisticXLPlay_V10.0_FP16.safetensors"

echo ""
echo "✅ Upload checkpoint SDXL completato."
echo "   Verifica nel volume:  aws s3 ls $DEST/ --region $DC --endpoint-url $ENDPOINT"
