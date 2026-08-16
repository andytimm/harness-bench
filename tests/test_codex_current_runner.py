from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harnessbench.codex_current_runner import (
    NAMESPACE, _binary_pin, _execution_config, _parser, _tree_hash,
)


class CodexCurrentRunnerTests(unittest.TestCase):
    def test_default_manifest_paths_are_plan_scoped(self) -> None:
        smoke = _parser().parse_args(["--plan", "smoke"])
        full = _parser().parse_args(["--plan", "full"])
        self.assertIsNone(smoke.manifest_dir)
        self.assertIsNone(full.manifest_dir)
        self.assertNotEqual(Path("evaluation/runs") / NAMESPACE / smoke.plan,
                            Path("evaluation/runs") / NAMESPACE / full.plan)

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
