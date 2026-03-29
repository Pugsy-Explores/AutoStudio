"""Fixed reranker constants (MiniLM / MS MARCO, CPU FP32)."""

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEVICE = "cpu"
PRECISION = "fp32"
ONNX_RELATIVE_PATH = "models/reranker/ms_marco_minilm_l6_v2_fp32/model.onnx"
TOKENIZER_MAX_LENGTH = 512
MAX_RERANK_SNIPPET_TOKENS = 256
MAX_RERANK_PAIR_TOKENS = 512
INFER_BATCH_SIZE = 16
