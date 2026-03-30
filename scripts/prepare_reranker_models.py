#!/usr/bin/env python3
"""Install ``cross-encoder/ms-marco-MiniLM-L-6-v2`` FP32 ONNX for CPU inference.

**Recommended on macOS (Apple Silicon):** download the official ONNX from the Hub
(``--source hub``). Local ``torch.onnx.export`` often crashes (SIGBUS / alignment) on
arm64; see PyTorch/transformers issues on ONNX + Apple Silicon.

Sources:
  hub     — ``hf_hub_download(..., "onnx/model.onnx")`` (default, no PyTorch export).
  optimum — ``optimum-cli export onnx`` (needs ``pip install "optimum[onnx]"``).
  torch   — legacy ``torch.onnx.export`` (last resort; may crash on Mac).

Requires (hub + verify): pip install huggingface_hub onnxruntime
Requires (torch): pip install torch transformers onnx onnxruntime

Example:
  python scripts/prepare_reranker_models.py
  python scripts/prepare_reranker_models.py --source optimum --output-dir models/reranker/ms_marco_minilm_l6_v2_fp32
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
HUB_ONNX_FILE = "onnx/model.onnx"


def _copy_from_hub(out_file: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:
        raise SystemExit(
            "huggingface_hub is required for --source hub. "
            "Install: pip install huggingface_hub"
        ) from e

    out_file.parent.mkdir(parents=True, exist_ok=True)
    cached = hf_hub_download(repo_id=MODEL_ID, filename=HUB_ONNX_FILE)
    shutil.copy2(cached, out_file)
    print(f"Copied Hub ONNX to {out_file}")


def _verify_onnx(path: Path) -> None:
    try:
        import onnxruntime as ort
    except ImportError:
        print("Skip verify: onnxruntime not installed", file=sys.stderr)
        return
    so = ort.SessionOptions()
    sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
    names = [i.name for i in sess.get_inputs()]
    print(f"OK: ONNX Runtime CPU load — inputs={names}")


def _export_optimum(out_file: Path) -> None:
    exe = shutil.which("optimum-cli")
    if not exe:
        raise SystemExit(
            "optimum-cli not found. Install: pip install \"optimum[onnx]\"\n"
            "Or use default: python scripts/prepare_reranker_models.py --source hub"
        )
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="optimum_onnx_") as tmp:
        tmp_path = Path(tmp)
        cmd = [
            exe,
            "export",
            "onnx",
            "--model",
            MODEL_ID,
            "--task",
            "text-classification",
            "--device",
            "cpu",
            "--dtype",
            "fp32",
            "--monolith",
            "--sequence-length",
            "512",
            "--opset",
            "17",
            str(tmp_path),
        ]
        print("Running:", " ".join(cmd), file=sys.stderr)
        subprocess.run(cmd, check=True, cwd=str(_ROOT))
        produced = tmp_path / "model.onnx"
        if not produced.is_file():
            raise SystemExit(f"optimum-cli did not produce {produced}")
        shutil.copy2(produced, out_file)
    print(f"Wrote {out_file} (optimum-cli export, FP32 CPU)")


def _export_torch_legacy(out_file: Path) -> None:
    """Last resort: torch.onnx.export — known to crash on some macOS/arm64 setups."""
    try:
        import torch
        import torch.nn as nn
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as e:
        raise SystemExit(f"Missing dependency: {e}") from e

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass

    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL_ID,
            attn_implementation="eager",
        )
    except TypeError:
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
        if hasattr(model.config, "attn_implementation"):
            model.config.attn_implementation = "eager"
    model = model.float().cpu()
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model.eval()

    enc = tokenizer(
        ["warmup query"],
        ["warmup passage"],
        padding="max_length",
        max_length=512,
        truncation=True,
        return_tensors="pt",
    )
    enc = {k: v.cpu() for k, v in enc.items()}

    class _Wrap(nn.Module):
        def __init__(self, m: nn.Module) -> None:
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, token_type_ids=None):
            out = self.m(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            )
            return out.logits

    wrapped = _Wrap(model)
    wrapped.eval()
    wrapped.cpu()

    _export_kw = {
        "export_params": True,
        "opset_version": 17,
        "do_constant_folding": True,
        "dynamo": False,
    }
    token_type = enc.get("token_type_ids")
    with torch.no_grad():
        if token_type is not None:
            torch.onnx.export(
                wrapped,
                (enc["input_ids"], enc["attention_mask"], token_type),
                str(out_file),
                input_names=["input_ids", "attention_mask", "token_type_ids"],
                output_names=["logits"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "seq"},
                    "attention_mask": {0: "batch", 1: "seq"},
                    "token_type_ids": {0: "batch", 1: "seq"},
                    "logits": {0: "batch"},
                },
                **_export_kw,
            )
        else:
            torch.onnx.export(
                wrapped,
                (enc["input_ids"], enc["attention_mask"]),
                str(out_file),
                input_names=["input_ids", "attention_mask"],
                output_names=["logits"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "seq"},
                    "attention_mask": {0: "batch", 1: "seq"},
                    "logits": {0: "batch"},
                },
                **_export_kw,
            )
    print(f"Wrote {out_file} (torch.onnx legacy export — avoid on Mac if unstable)")


def main() -> int:
    p = argparse.ArgumentParser(description="Prepare MS MARCO MiniLM cross-encoder ONNX (FP32 CPU).")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=_ROOT / "models" / "reranker" / "ms_marco_minilm_l6_v2_fp32",
        help="Directory for model.onnx",
    )
    p.add_argument(
        "--source",
        choices=("hub", "optimum", "torch"),
        default="hub",
        help="hub=download official ONNX (default, best on macOS); optimum=optimum-cli; torch=local export",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip ONNX Runtime load check",
    )
    args = p.parse_args()

    out_file = args.output_dir / "model.onnx"

    if args.source == "hub":
        _copy_from_hub(out_file)
    elif args.source == "optimum":
        _export_optimum(out_file)
    else:
        print(
            "WARNING: --source torch can SIGBUS on Apple Silicon; prefer --source hub.",
            file=sys.stderr,
        )
        _export_torch_legacy(out_file)

    if not args.no_verify:
        _verify_onnx(out_file)

    print(f"Done: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
