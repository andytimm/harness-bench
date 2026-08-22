from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "prime_agent_version.py"
SPEC = importlib.util.spec_from_file_location("prime_agent_version", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
prime_agent_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prime_agent_version)


class PrimeEvaluationVersionTests(unittest.TestCase):
    def test_reads_version_from_stderr(self) -> None:
        completed = subprocess.CompletedProcess(["prime-agent", "--version"], 0, "", "0.7.2\n")
        with patch.object(prime_agent_version.subprocess, "run", return_value=completed) as run:
            self.assertEqual(prime_agent_version.installed_prime_agent_version(), "0.7.2")
        run.assert_called_once_with(
            ["prime-agent", "--version"], text=True, capture_output=True, check=False, timeout=10
        )

    def test_mismatch_requires_explicit_override(self) -> None:
        completed = subprocess.CompletedProcess(["prime-agent", "--version"], 0, "prime-agent 0.8.0\n", "")
        with patch.object(prime_agent_version.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "requires prime-agent 0.7.2; found 0.8.0"):
                prime_agent_version.require_historical_prime_agent_version()

    def test_mismatch_can_be_explicitly_allowed(self) -> None:
        completed = subprocess.CompletedProcess(["prime-agent", "--version"], 0, "0.8.0\n", "")
        with patch.object(prime_agent_version.subprocess, "run", return_value=completed):
            self.assertEqual(
                prime_agent_version.require_historical_prime_agent_version(allow_mismatch=True), "0.8.0"
            )

    def test_nonzero_version_command_is_rejected(self) -> None:
        completed = subprocess.CompletedProcess(["prime-agent", "--version"], 1, "", "broken install")
        with patch.object(prime_agent_version.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "exited 1: broken install"):
                prime_agent_version.installed_prime_agent_version()
