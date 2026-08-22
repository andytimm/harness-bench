"""Version guard for reproducing the historical Prime Agent evaluation."""

from __future__ import annotations

import re
import subprocess


EXPECTED_PRIME_AGENT_VERSION = "0.7.2"
_VERSION_PATTERN = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b")


def installed_prime_agent_version(command: str = "prime-agent") -> str:
    """Return the semantic version reported by a Prime Agent CLI command."""
    try:
        completed = subprocess.run(
            [command, "--version"], text=True, capture_output=True, check=False, timeout=10
        )
    except OSError as exc:
        raise RuntimeError(f"could not run {command!r} --version: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out running {command!r} --version") from exc

    output = (completed.stdout.strip() or completed.stderr.strip())
    if completed.returncode != 0:
        raise RuntimeError(
            f"{command!r} --version exited {completed.returncode}: {output or 'no output'}"
        )
    match = _VERSION_PATTERN.search(output)
    if not match:
        raise RuntimeError(f"could not parse a semantic version from {command!r}: {output or 'no output'}")
    return match.group(1)


def require_historical_prime_agent_version(
    *, command: str = "prime-agent", allow_mismatch: bool = False
) -> str:
    """Enforce the CLI version used for the published historical evaluation."""
    actual = installed_prime_agent_version(command)
    if actual != EXPECTED_PRIME_AGENT_VERSION and not allow_mismatch:
        raise RuntimeError(
            f"historical Prime evaluation requires prime-agent {EXPECTED_PRIME_AGENT_VERSION}; "
            f"found {actual}. Install the historical version or rerun with "
            "--allow-version-mismatch to record a non-identical reproduction."
        )
    if actual != EXPECTED_PRIME_AGENT_VERSION:
        print(
            f"[prime-version] allowing mismatch: expected {EXPECTED_PRIME_AGENT_VERSION}, found {actual}",
            flush=True,
        )
    return actual
