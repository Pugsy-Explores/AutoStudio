#!/usr/bin/env bash
set -euo pipefail

############################
# CONFIG
############################

MODEL_DIR="/opt/llama/models"
LLAMA_BIN="/opt/llama/bin/llama-server"

PREFIX="/opt/litellm"
CONFIG="$PREFIX/router.yaml"

REASON_MODEL="$MODEL_DIR/deepseek-r1-distill-qwen-32b-q6_k.gguf"
CODER_MODEL="$MODEL_DIR/Qwen2.5-Coder-32B-Q6_K.gguf"

REASON_PORT=9000
CODER_PORT=9001
ROUTER_PORT=8000

############################
# LOGGING
############################

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

############################
# UTIL
############################

port_open() {
  ss -ltn "sport = :$1" | grep -q LISTEN
}

wait_for_service() {
  local port=$1
  for i in {1..60}; do
    if curl -s "http://localhost:${port}/v1/models" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

############################
# DEPENDENCIES
############################

log "Ensuring LiteLLM installed"

if ! command -v litellm >/dev/null 2>&1; then
  pip install -q --upgrade pip
  pip install -q "litellm[proxy]" websockets uvicorn pyyaml
fi

############################
# VALIDATE MODELS
############################

[ -f "$REASON_MODEL" ] || { echo "Missing model: $REASON_MODEL"; exit 1; }
[ -f "$CODER_MODEL" ] || { echo "Missing model: $CODER_MODEL"; exit 1; }

############################
# CONFIG FILE
############################

log "Preparing LiteLLM config"

mkdir -p "$PREFIX"

cat <<EOF > "$CONFIG"
model_list:
  - model_name: reasoning
    litellm_params:
      model: openai/reasoning
      api_base: http://localhost:${REASON_PORT}/v1
      api_key: none

  - model_name: coder
    litellm_params:
      model: openai/coder
      api_base: http://localhost:${CODER_PORT}/v1
      api_key: none

router_settings:
  routing_strategy: simple-shuffle
EOF

############################
# START REASONING MODEL
############################

if port_open "$REASON_PORT"; then
  log "Reasoning model already running on :$REASON_PORT"
else
  log "Starting reasoning model"
  "$LLAMA_BIN" \
    -m "$REASON_MODEL" \
    -ngl 80 \
    --flash-attn on \
    -c 32768 \
    --port "$REASON_PORT" \
    --host 0.0.0.0 \
    --alias reasoning \
    --parallel 4 \
    --timeout 600 \
    > /tmp/reasoning.log 2>&1 &
fi

############################
# START CODER MODEL
############################

if port_open "$CODER_PORT"; then
  log "Coder model already running on :$CODER_PORT"
else
  log "Starting coder model"
  "$LLAMA_BIN" \
    -m "$CODER_MODEL" \
    -ngl 80 \
    --flash-attn on \
    -c 32768 \
    --port "$CODER_PORT" \
    --host 0.0.0.0 \
    --alias coder \
    --parallel 4 \
    --timeout 600 \
    > /tmp/coder.log 2>&1 &
fi

############################
# WAIT FOR MODELS
############################

log "Waiting for reasoning model"
wait_for_service "$REASON_PORT" || { echo "Reasoning server failed"; exit 1; }

log "Waiting for coder model"
wait_for_service "$CODER_PORT" || { echo "Coder server failed"; exit 1; }

############################
# START ROUTER
############################

if port_open "$ROUTER_PORT"; then
  log "LiteLLM router already running on :$ROUTER_PORT"
else
  log "Starting LiteLLM router"
  litellm --config "$CONFIG" --port "$ROUTER_PORT"
fi