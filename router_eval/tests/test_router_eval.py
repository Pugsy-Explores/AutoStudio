"""
Tests for router_eval harness. Mocks LLM calls so no live server required.
Verifies: dataset loading, router imports, metrics (accuracy, latency), error handling.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router_eval.dataset import CATEGORIES, load_dataset
from router_eval.router_eval import (
    _calibration_buckets,
    _extract_category,
    run_eval,
)
from router_eval.run_all_routers import ROUTERS


class TestDataset(unittest.TestCase):
    """Dataset loading and structure."""

    def test_categories(self):
        self.assertEqual(CATEGORIES, ("EDIT", "SEARCH", "EXPLAIN", "INFRA", "GENERAL"))

    def test_load_builtin(self):
        data = load_dataset()
        self.assertGreater(len(data), 0, "Built-in dataset must not be empty")
        for item in data[:10]:
            self.assertIn("instruction", item)
            self.assertIn("expected_category", item)
            self.assertIn(item["expected_category"], CATEGORIES)

    def test_load_from_json_file(self):
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
            data = load_dataset(path)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["expected_category"], "SEARCH")
            self.assertEqual(data[1]["expected_category"], "EDIT")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_from_jsonl_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"instruction": "Find X.", "expected_category": "SEARCH"}\n')
            f.write('{"instruction": "Modify Y.", "expected_category": "EDIT"}\n')
            path = f.name
        try:
            data = load_dataset(path)
            self.assertEqual(len(data), 2)
        finally:
            Path(path).unlink(missing_ok=True)


class TestExtractCategory(unittest.TestCase):
    """Category extraction from route outputs."""

    def test_dict_with_category(self):
        self.assertEqual(_extract_category({"category": "SEARCH", "confidence": 0.8}), "SEARCH")

    def test_dict_with_primary_only(self):
        self.assertEqual(_extract_category({"primary": "EDIT"}), "EDIT")

    def test_string(self):
        self.assertEqual(_extract_category("EXPLAIN"), "EXPLAIN")

    def test_dict_fallback_general(self):
        self.assertEqual(_extract_category({"other": "x"}), "GENERAL")


class TestCalibrationBuckets(unittest.TestCase):
    """Calibration bucket computation."""

    def test_buckets(self):
        conf = [0.1, 0.3, 0.5, 0.7, 0.9]
        correct = [True, False, True, False, True]
        buckets = _calibration_buckets(conf, correct)
        self.assertIn(0, buckets)
        self.assertIn(2, buckets)
        self.assertEqual(buckets[0]["count"], 1)
        self.assertEqual(buckets[2]["count"], 1)

    def test_empty(self):
        self.assertEqual(_calibration_buckets([], []), {})


def _mock_llama_simple(response: str = "EDIT"):
    """Return a mock that always returns the given response (for baseline/fewshot/ensemble)."""

    def mock_fn(*args, **kwargs):
        return response

    return mock_fn


def _mock_llama_confidence(response: str = "EDIT 0.85"):
    """Return a mock for confidence_router (category + confidence)."""
    return _mock_llama_simple(response)


def _mock_llama_dual(response: str = "EDIT SEARCH 0.82"):
    """Return a mock for dual_router (primary secondary confidence)."""
    return _mock_llama_simple(response)


def _mock_llama_critic(response: str = "YES"):
    """Return a mock for critic response (YES or NO CATEGORY)."""
    return _mock_llama_simple(response)


class TestRunEvalMetrics(unittest.TestCase):
    """Run eval produces correct metrics structure."""

    def _run_with_mock_route(self, route_fn, dataset_path=None):
        """Helper: run_eval with a simple mock route returning EDIT for all."""
        return run_eval(
            dataset_path=dataset_path,
            verbose=False,
            route_fn=route_fn,
            router_name="mock",
        )

    def test_run_eval_returns_metrics(self):
        def mock_route(instruction):
            return "EDIT"

        metrics = self._run_with_mock_route(mock_route)
        self.assertIn("accuracy", metrics)
        self.assertIn("correct", metrics)
        self.assertIn("total", metrics)
        self.assertIn("confusion", metrics)
        self.assertIn("avg_latency_sec", metrics)
        self.assertEqual(metrics["total"], len(load_dataset()))
        self.assertGreaterEqual(metrics["correct"], 0)
        self.assertLessEqual(metrics["correct"], metrics["total"])
        self.assertGreaterEqual(metrics["avg_latency_sec"], 0)

    def test_classification_accuracy_bounds(self):
        def mock_route(instruction):
            return "EDIT"

        metrics = self._run_with_mock_route(mock_route)
        self.assertGreaterEqual(metrics["accuracy"], 0.0)
        self.assertLessEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["accuracy"], metrics["correct"] / metrics["total"])

    def test_classification_accuracy_assertion(self):
        """Assert classification accuracy is computed and within valid range."""
        def mock_route(instruction):
            return "EDIT"

        metrics = self._run_with_mock_route(mock_route)
        accuracy = metrics["accuracy"]
        self.assertIsInstance(accuracy, (int, float))
        self.assertGreaterEqual(accuracy, 0.0, "Accuracy must be >= 0")
        self.assertLessEqual(accuracy, 1.0, "Accuracy must be <= 1")
        self.assertEqual(
            accuracy,
            metrics["correct"] / metrics["total"],
            "Accuracy must equal correct/total",
        )

    def test_latency_recorded(self):
        def mock_route(instruction):
            return "GENERAL"

        metrics = self._run_with_mock_route(mock_route)
        self.assertIn("avg_latency_sec", metrics)
        self.assertIsInstance(metrics["avg_latency_sec"], (int, float))
        self.assertGreaterEqual(metrics["avg_latency_sec"], 0)

    def test_latency_assertion(self):
        """Assert latency is recorded per item and average is non-negative."""
        def mock_route(instruction):
            return "EDIT"

        metrics = self._run_with_mock_route(mock_route)
        avg_latency = metrics["avg_latency_sec"]
        self.assertIsInstance(avg_latency, (int, float))
        self.assertGreaterEqual(avg_latency, 0, "Avg latency must be >= 0")
        self.assertEqual(metrics["total"], len(load_dataset()))

    def test_confusion_matrix_structure(self):
        def mock_route(instruction):
            return "SEARCH"

        metrics = self._run_with_mock_route(mock_route)
        self.assertIsInstance(metrics["confusion"], dict)
        for expected, pred_counts in metrics["confusion"].items():
            self.assertIn(expected, CATEGORIES)
            self.assertIsInstance(pred_counts, dict)
            for pred, count in pred_counts.items():
                self.assertIn(pred, CATEGORIES)
                self.assertIsInstance(count, int)
                self.assertGreaterEqual(count, 0)


# Patch targets: routers import llama_chat at load time, so we must patch where they use it
PATCH_BASELINE = "router_eval.routers.baseline_router.llama_chat"
PATCH_FEWSHOT = "router_eval.routers.fewshot_router.llama_chat"
PATCH_ENSEMBLE = "router_eval.routers.ensemble_router.llama_chat"
PATCH_CONFIDENCE = "router_eval.routers.confidence_router.llama_chat"
PATCH_ROUTER_CORE = "router_eval.utils.router_core.llama_chat"
PATCH_CRITIC = "router_eval.routers.critic_router.llama_chat"
PATCH_FINAL = "router_eval.routers.final_router.llama_chat"


class TestAllRoutersRun(unittest.TestCase):
    """Verify all 7 routers can be imported and run (with mocked LLM)."""

    def test_all_routers_listed(self):
        expected_names = [
            "baseline",
            "fewshot",
            "ensemble",
            "confidence",
            "dual",
            "critic",
            "final",
        ]
        actual_names = [name for name, _ in ROUTERS]
        for name in expected_names:
            self.assertIn(name, actual_names, f"Router {name} must be in ROUTERS")

    @patch(PATCH_BASELINE)
    def test_baseline_router_runs(self, mock_llama):
        mock_llama.return_value = "EDIT"
        from router_eval.routers import baseline_router

        result = baseline_router.route("Add retry logic.")
        self.assertEqual(result, "EDIT")
        self.assertEqual(baseline_router.ROUTER_NAME, "baseline")

    @patch(PATCH_FEWSHOT)
    def test_fewshot_router_runs(self, mock_llama):
        mock_llama.return_value = "SEARCH"
        from router_eval.routers import fewshot_router

        result = fewshot_router.route("Find the login handler.")
        self.assertEqual(result, "SEARCH")
        self.assertEqual(fewshot_router.ROUTER_NAME, "fewshot")

    @patch(PATCH_ENSEMBLE)
    def test_ensemble_router_runs(self, mock_llama):
        mock_llama.return_value = "EXPLAIN"
        from router_eval.routers import ensemble_router

        result = ensemble_router.route("What does the API expect?")
        self.assertEqual(result, "EXPLAIN")
        self.assertEqual(ensemble_router.ROUTER_NAME, "ensemble")

    @patch(PATCH_CONFIDENCE)
    def test_confidence_router_runs(self, mock_llama):
        mock_llama.return_value = "INFRA 0.9"
        from router_eval.routers import confidence_router

        result = confidence_router.route("Add Dockerfile.")
        self.assertIn("category", result)
        self.assertIn("confidence", result)
        self.assertEqual(result["category"], "INFRA")
        self.assertEqual(confidence_router.ROUTER_NAME, "confidence")

    @patch(PATCH_ROUTER_CORE)
    def test_dual_router_runs(self, mock_llama):
        mock_llama.return_value = "EDIT SEARCH 0.85"
        from router_eval.routers import dual_router

        result = dual_router.route("Refactor validation.")
        self.assertIn("category", result)
        self.assertIn("primary", result)
        self.assertIn("secondary", result)
        self.assertIn("confidence", result)
        self.assertEqual(dual_router.ROUTER_NAME, "dual")

    @patch(PATCH_CRITIC)
    @patch(PATCH_ROUTER_CORE)
    def test_critic_router_runs(self, mock_core, mock_critic):
        mock_core.return_value = "EDIT SEARCH 0.5"
        mock_critic.return_value = "YES"
        from router_eval.routers import critic_router

        result = critic_router.route("Ambiguous instruction.")
        self.assertIn("category", result)
        self.assertIn("confidence", result)
        self.assertEqual(critic_router.ROUTER_NAME, "critic")

    @patch(PATCH_FINAL)
    @patch(PATCH_ROUTER_CORE)
    def test_final_router_runs(self, mock_core, mock_final):
        mock_core.return_value = "EDIT SEARCH 0.5"
        mock_final.return_value = "YES"
        from router_eval.routers import final_router

        result = final_router.route("Some instruction.")
        self.assertIn("category", result)
        self.assertIn("confidence", result)
        self.assertEqual(final_router.ROUTER_NAME, "final")


class TestRunEvalWithAllRouters(unittest.TestCase):
    """Run full eval with each router (mocked) and assert metrics."""

    def _run_router_eval(self, name, route_fn):
        return run_eval(
            dataset_path=None,
            verbose=False,
            route_fn=route_fn,
            router_name=name,
        )

    @patch(PATCH_FINAL)
    @patch(PATCH_CRITIC)
    @patch(PATCH_ROUTER_CORE)
    @patch(PATCH_CONFIDENCE)
    @patch(PATCH_ENSEMBLE)
    @patch(PATCH_FEWSHOT)
    @patch(PATCH_BASELINE)
    def test_each_router_produces_valid_metrics(
        self, mock_base, mock_few, mock_ens, mock_conf, mock_rc, mock_crit, mock_fin
    ):
        """Each router must complete eval and return valid metrics."""
        mock_base.return_value = "EDIT"
        mock_few.return_value = "EDIT"
        mock_ens.return_value = "EDIT"
        mock_conf.return_value = "EDIT 0.9"
        mock_rc.return_value = "EDIT EDIT 0.5"
        mock_crit.return_value = "YES"
        mock_fin.return_value = "YES"
        for name, route_fn in ROUTERS:
            with self.subTest(router=name):
                metrics = self._run_router_eval(name, route_fn)
                self.assertIsNotNone(metrics, f"{name} must return metrics")
                self.assertEqual(metrics["total"], len(load_dataset()))
                self.assertGreaterEqual(metrics["correct"], 0)
                self.assertLessEqual(metrics["correct"], metrics["total"])
                self.assertGreaterEqual(metrics["avg_latency_sec"], 0)
                self.assertIn("confusion", metrics)


class TestErrorHandling(unittest.TestCase):
    """Error handling in route and eval."""

    def test_route_raises_propagates(self):
        """Assert router exceptions propagate to caller (no silent swallow)."""
        def failing_route(instruction):
            raise ValueError("Simulated router failure")

        with self.assertRaises(ValueError) as ctx:
            run_eval(
                verbose=False,
                route_fn=failing_route,
                router_name="failing",
            )
        self.assertIn("Simulated router failure", str(ctx.exception))

    def test_error_handling_assertion(self):
        """Assert different error types propagate correctly."""
        for exc_cls, msg in [
            (ValueError, "Value error"),
            (RuntimeError, "Runtime error"),
        ]:
            def failing(instruction, e=exc_cls, m=msg):
                raise e(m)

            with self.assertRaises(exc_cls) as ctx:
                run_eval(verbose=False, route_fn=failing, router_name="err")
            self.assertIn(msg, str(ctx.exception))

    def test_route_returns_invalid_category_fallback(self):
        """Router returning unknown category gets parsed to GENERAL or first word."""
        def weird_route(instruction):
            return "UNKNOWN_CAT"

        metrics = run_eval(
            verbose=False,
            route_fn=weird_route,
            router_name="weird",
        )
        # Harness should not crash; parse_category falls back to GENERAL
        self.assertEqual(metrics["total"], len(load_dataset()))
        self.assertIn("confusion", metrics)

    def test_empty_dataset_path_returns_builtin(self):
        data = load_dataset(None)
        self.assertGreater(len(data), 0)


class TestMainModuleMock(unittest.TestCase):
    """Verify main module runs with --mock (no LLM required)."""

    def test_main_with_mock_returns_metrics(self):
        """Run eval via run_eval with mock route; assert metrics computed."""
        metrics = run_eval(
            verbose=False,
            route_fn=lambda instr: "EDIT",
            router_name="mock",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["total"], len(load_dataset()))
        self.assertGreaterEqual(metrics["accuracy"], 0.0)
        self.assertLessEqual(metrics["accuracy"], 1.0)
        self.assertGreaterEqual(metrics["avg_latency_sec"], 0.0)


if __name__ == "__main__":
    unittest.main()
