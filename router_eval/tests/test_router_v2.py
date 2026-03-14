"""
Tests for router_v2 and router_eval_v2. No LLM required (mocked).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router_eval.dataset_v2 import (
    ADVERSARIAL_DATASET_PATH,
    CATEGORIES_V2,
    GOLDEN_DATASET_PATH,
    load_dataset_v2,
)
from router_eval.router_eval_v2 import _calibration_buckets, _extract_category, run_eval_v2
from router_eval.routers.router_v2 import route, route_with_fallback


class TestDatasetV2(unittest.TestCase):
    def test_categories_v2(self):
        self.assertEqual(CATEGORIES_V2, ("EDIT", "SEARCH", "EXPLAIN", "INFRA"))

    def test_load_normal(self):
        data = load_dataset_v2()
        self.assertGreater(len(data), 0)
        for item in data[:5]:
            self.assertIn("instruction", item)
            self.assertIn("expected_category", item)
            self.assertIn(item["expected_category"], CATEGORIES_V2)

    def test_load_golden(self):
        self.assertTrue(GOLDEN_DATASET_PATH.exists(), f"Golden file missing: {GOLDEN_DATASET_PATH}")
        data = load_dataset_v2(use_golden=True)
        self.assertGreater(len(data), 0)
        for item in data[:3]:
            self.assertIn(item["expected_category"], CATEGORIES_V2)

    def test_load_adversarial(self):
        self.assertTrue(
            ADVERSARIAL_DATASET_PATH.exists(), f"Adversarial file missing: {ADVERSARIAL_DATASET_PATH}"
        )
        data = load_dataset_v2(use_adversarial=True)
        self.assertGreater(len(data), 0)

    def test_load_from_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                [
                    {"instruction": "Find the login handler.", "expected_category": "SEARCH"},
                    {"instruction": "Add retry logic.", "expected_category": "EDIT"},
                ],
                f,
            )
            path = f.name
        try:
            data = load_dataset_v2(path=path)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["expected_category"], "SEARCH")
            self.assertEqual(data[1]["expected_category"], "EDIT")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_use_adversarial_overrides_path(self):
        data = load_dataset_v2(path="/nonexistent.json", use_adversarial=True)
        self.assertGreater(len(data), 0)

    def test_use_golden_overrides_path(self):
        data = load_dataset_v2(path="/nonexistent.json", use_golden=True)
        self.assertGreater(len(data), 0)


class TestRouterV2(unittest.TestCase):
    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_parses_valid_response(self, mock_llama):
        mock_llama.return_value = "SEARCH 0.82"
        out = route("Find the login handler.")
        self.assertEqual(out["category"], "SEARCH")
        self.assertEqual(out["confidence"], 0.82)

    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_parses_all_categories(self, mock_llama):
        for cat in CATEGORIES_V2:
            mock_llama.return_value = f"{cat} 0.9"
            out = route("any")
            self.assertEqual(out["category"], cat)
            self.assertEqual(out["confidence"], 0.9)

    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_clamps_confidence(self, mock_llama):
        mock_llama.return_value = "EDIT 1.5"
        out = route("any")
        self.assertEqual(out["confidence"], 1.0)
        mock_llama.return_value = "EDIT -0.1"
        out = route("any")
        self.assertEqual(out["confidence"], 0.0)

    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_fallback_on_parse_failure(self, mock_llama):
        mock_llama.return_value = "GARBAGE"
        out = route("any")
        self.assertEqual(out["category"], "EXPLAIN")
        self.assertEqual(out["confidence"], 0.0)

    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_with_fallback_above_threshold(self, mock_llama):
        mock_llama.return_value = "EDIT 0.8"
        out = route_with_fallback("any", threshold=0.6)
        self.assertEqual(out["category"], "EDIT")
        self.assertEqual(out["confidence"], 0.8)
        self.assertNotIn("fallback", out)

    @patch("router_eval.routers.router_v2.llama_chat")
    def test_route_with_fallback_below_threshold(self, mock_llama):
        mock_llama.return_value = "EDIT 0.4"
        out = route_with_fallback("any", threshold=0.6)
        self.assertEqual(out["category"], "EXPLAIN")
        self.assertEqual(out["confidence"], 0.4)
        self.assertTrue(out["fallback"])


class TestRouterEvalV2(unittest.TestCase):
    def test_extract_category(self):
        self.assertEqual(_extract_category({"category": "SEARCH", "confidence": 0.8}), "SEARCH")
        self.assertEqual(_extract_category({"category": "EXPLAIN"}), "EXPLAIN")

    def test_calibration_buckets(self):
        conf = [0.1, 0.3, 0.5, 0.7, 0.9]
        correct = [True, False, True, False, True]
        buckets = _calibration_buckets(conf, correct)
        self.assertIn(0, buckets)
        self.assertIn(2, buckets)
        self.assertEqual(buckets[0]["count"], 1)
        self.assertEqual(buckets[2]["count"], 1)

    @patch("router_eval.router_eval_v2.route")
    def test_run_eval_v2_metrics(self, mock_route):
        mock_route.return_value = {"category": "EDIT", "confidence": 0.9}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                [
                    {"instruction": "Add retry.", "expected_category": "EDIT"},
                    {"instruction": "Find X.", "expected_category": "SEARCH"},
                ],
                f,
            )
            path = f.name
        try:
            metrics = run_eval_v2(
                dataset_path=path,
                verbose=False,
                save_plots=False,
            )
            self.assertEqual(metrics["total"], 2)
            self.assertEqual(metrics["correct"], 1)  # first matches EDIT, second we return EDIT
            self.assertEqual(metrics["accuracy"], 0.5)
            self.assertIn("confusion", metrics)
            self.assertIn("avg_confidence", metrics)
        finally:
            Path(path).unlink(missing_ok=True)

    @patch("router_eval.router_eval_v2.route")
    def test_run_eval_v2_with_golden_flag(self, mock_route):
        mock_route.return_value = {"category": "EXPLAIN", "confidence": 0.5}
        metrics = run_eval_v2(use_golden=True, verbose=False, save_plots=False)
        self.assertGreater(metrics["total"], 0)

    @patch("router_eval.router_eval_v2.route")
    def test_run_eval_v2_with_adversarial_flag(self, mock_route):
        mock_route.return_value = {"category": "SEARCH", "confidence": 0.7}
        metrics = run_eval_v2(use_adversarial=True, verbose=False, save_plots=False)
        self.assertGreater(metrics["total"], 0)


if __name__ == "__main__":
    unittest.main()
