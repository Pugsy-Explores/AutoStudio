#!/usr/bin/env bash
set -e

MODEL_DIR="/opt/llama/models"

echo "Creating model directory"
mkdir -p $MODEL_DIR
cd $MODEL_DIR

echo "Enabling fast HuggingFace downloads"
export HF_HUB_ENABLE_HF_TRANSFER=1

echo "Installing HF CLI if missing"
pip install -q -U huggingface_hub

echo "Downloading CODING model (Qwen2.5-Coder-32B Q6)"
hf download 
bartowski/Qwen2.5-Coder-32B-GGUF 
Qwen2.5-Coder-32B-Q6_K.gguf 
--local-dir $MODEL_DIR

echo "Downloading REASONING model (DeepSeek-R1 Distill Q6)"
hf download 
roleplaiapp/DeepSeek-R1-Distill-Qwen-32B-Q6_K-GGUF 
DeepSeek-R1-Distill-Qwen-32B-Q6_K.gguf 
--local-dir $MODEL_DIR

echo ""
echo "Download complete."
echo ""
ls -lh $MODEL_DIR
