llama-server \
  -m ~/Library/Caches/llama.cpp/bartowski_Qwen2.5-7B-Instruct-GGUF_Qwen2.5-7B-Instruct-Q5_K_M.gguf \
  --port 8081 \
  -c 16879 \
  -b 128 \
  -ub 64 \
  -ngl 99 \
  -fa on \
  --parallel 1 \
  --threads 6 \
  --threads-batch 6 \
  --cont-batching

-- below one is working --
llama-server \
  -m ~/Library/Caches/llama.cpp/unsloth_DeepSeek-R1-Distill-Qwen-7B-GGUF_DeepSeek-R1-Distill-Qwen-7B-Q5_K_M.gguf \
  --port 8081 \
  -c 32567 \
  -ngl 50 \
  -fa on \
  --parallel 2 \
  --cont-batching --jinja