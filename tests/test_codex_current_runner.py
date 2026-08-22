from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harnessbench.codex_current_runner import (
    CORRECTION_023_NETWORK_PLAN, CORRECTION_023_PLAN, NAMESPACE, _acquire_run_lock, _binary_pin, _correction_basis,
    _correction_visit_validation, _execution_config, _manifest_dir_allowed, _parser,
    _prepend_interpreter_bin_to_path, _tree_hash,
)
from harnessbench.config import load_model_config


class CodexCurrentRunnerTests(unittest.TestCase):
    def test_default_manifest_paths_are_plan_scoped(self) -> None:
        smoke = _parser().parse_args(["--plan", "smoke"])
        full = _parser().parse_args(["--plan", "full"])
        correction = _parser().parse_args(["--plan", CORRECTION_023_PLAN])
        network_correction = _parser().parse_args(["--plan", CORRECTION_023_NETWORK_PLAN])
        self.assertIsNone(smoke.manifest_dir)
        self.assertIsNone(full.manifest_dir)
        self.assertIsNone(correction.manifest_dir)
        self.assertIsNone(network_correction.manifest_dir)
        self.assertEqual(smoke.harness_config, Path("config/harness.example.yaml"))
        self.assertNotEqual(Path("evaluation/runs") / NAMESPACE / smoke.plan,
                            Path("evaluation/runs") / NAMESPACE / full.plan)
        self.assertNotEqual(Path("evaluation/runs") / NAMESPACE / correction.plan,
                            Path("evaluation/runs") / NAMESPACE / full.plan)
        self.assertNotEqual(Path("evaluation/runs") / NAMESPACE / network_correction.plan,
                            Path("evaluation/runs") / NAMESPACE / correction.plan)

    def test_current_config_allows_only_required_nonsensitive_hook_url(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        config = load_model_config(repository / "config" / "harness.example.yaml")[NAMESPACE]
        self.assertEqual(config.get("allowed_hook_env"), ["MOCK_FORM_URL"])
        self.assertIs(config.get("sandbox_network_access"), True)

    def test_correction_basis_is_bound_to_preserved_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            run_root = repository / "evaluation" / "runs" / NAMESPACE / "full"
            run_root.mkdir(parents=True)
            manifest = {"namespace": NAMESPACE, "plan": "full", "entries": {
                "023-web-form-extraction": {"status": "succeeded", "attempt": 1}}}
            manifest_path = run_root / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest))
            audit = {"verdict": "operational_correction_required", "run": {
                "namespace": NAMESPACE, "plan": "full", "manifest": str(manifest_path),
                "manifest_sha256_before_audit_record": __import__("hashlib").sha256(manifest_path.read_bytes()).hexdigest()},
                "operational_blocker": {"task_id": "023-web-form-extraction",
                "retry_basis": "operational adapter/integration failure identified from trace and HTTP evidence, not outcome quality"}}
            (run_root / "PROVENANCE_RECORDED.json").write_text(__import__("json").dumps(audit))
            result = repository / "results" / NAMESPACE / "gpt-5.4" / "023-web-form-extraction.json"
            result.parent.mkdir(parents=True); result.write_text("{}")
            basis = _correction_basis(repository, CORRECTION_023_PLAN, repository / "results")
            self.assertEqual(basis["task_id"], "023-web-form-extraction")
            self.assertTrue(basis["initial_attempt_retained"])
            self.assertEqual(len(basis["initial_manifest_sha256"]), 64)
            self.assertEqual(len(basis["provenance_audit_sha256"]), 64)
            manifest_path.write_text(__import__("json").dumps(manifest | {"tampered": True}))
            with self.assertRaises(SystemExit):
                _correction_basis(repository, CORRECTION_023_PLAN, repository / "results")

    def test_correction_requires_real_local_service_visits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "out").mkdir()
            result = type("Result", (), {})()
            result.workspace = workspace
            result.oracle_result = {"checks": [
                {"id": "visited_index", "pass": True},
                {"id": "visited_lookup", "pass": True},
                {"id": "visited_confirm", "pass": False},
            ]}
            (workspace / "out" / "form_access.log").write_text("/\n/lookup\n")
            self.assertFalse(_correction_visit_validation(result)["valid"])
            result.oracle_result["checks"][2]["pass"] = True
            (workspace / "out" / "form_access.log").write_text("/\n/lookup\n/confirm\n")
            self.assertTrue(_correction_visit_validation(result)["valid"])


    def test_project_interpreter_bin_is_first_on_path(self) -> None:
        interpreter_bin = str(Path(sys.executable).parent)
        with patch.dict(os.environ, {"PATH": f"/usr/bin{os.pathsep}{interpreter_bin}{os.pathsep}/bin"}):
            _prepend_interpreter_bin_to_path()
            self.assertEqual(os.environ["PATH"].split(os.pathsep), [interpreter_bin, "/usr/bin", "/bin"])

    def test_custom_manifest_dir_must_be_external_or_ignored(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        self.assertTrue(_manifest_dir_allowed(repository, repository / "evaluation" / "runs" / "custom"))
        self.assertFalse(_manifest_dir_allowed(repository, repository / "not-ignored-manifest"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(_manifest_dir_allowed(repository, Path(tmp) / "manifest"))

    def test_same_run_directory_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = _acquire_run_lock(Path(tmp))
            try:
                with self.assertRaises(SystemExit):
                    _acquire_run_lock(Path(tmp))
            finally:
                first.close()

    def test_task_tree_hash_detects_fixture_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "fixtures").mkdir()
            fixture = root / "fixtures" / "input.txt"; fixture.write_text("one")
            before = _tree_hash(root); fixture.write_text("two")
            self.assertNotEqual(before, _tree_hash(root))

    def test_manifest_binary_pin_becomes_mandatory_adapter_expectations(self) -> None:
        preflight = {"provenance": {"version":"codex-cli 0.139.0", "resolved":"/launcher",
            "sha256":"a", "native_executable":"/native", "native_sha256":"b"}}
        binary = _binary_pin(preflight)
        config = _execution_config({"model":"gpt-5.4"}, binary)
        self.assertEqual(config["expected_resolved_executable"], "/launcher")
        self.assertEqual(config["expected_executable_sha256"], "a")
        self.assertEqual(config["expected_native_executable"], "/native")
        self.assertEqual(config["expected_native_sha256"], "b")


if __name__ == "__main__":
    unittest.main()
