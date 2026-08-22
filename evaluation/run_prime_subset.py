#!/usr/bin/env python3
"""Run the frozen 47-task Prime Agent GPT-5.4 subset sequentially."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnessbench.config import load_app_config
from prime_agent_version import EXPECTED_PRIME_AGENT_VERSION, require_historical_prime_agent_version


SUBSET_CLASSES = [
    "Software Engineering & Codebase Maintenance",
    "Data, BI & Finance Analytics",
    "Long-running Autonomy & State Adaptation",
]
SUBSET_TASKS = [
    "007-session-memory",
    "009-git-pr-merge",
    "011-code-debug",
    "014-task-decomposition",
    "016-code-repair-pytest",
    "017-db-doc-consistency",
    "018-provider-failover-audit",
    "039-repo-architecture-map",
    "040-test-coverage-fill",
    "041-frontend-state-bug",
    "042-api-schema-migration",
    "043-db-migration-safety",
    "044-ci-config-repair",
    "045-dependency-upgrade-compat",
    "046-performance-regression",
    "047-code-review-risk-report",
    "048-release-note-changelog",
    "049-excel-like-cleaning",
    "050-multitable-join-analysis",
    "051-sql-query-report",
    "052-metric-definition-audit",
    "053-anomalous-transaction-detect",
    "054-budget-variance-analysis",
    "055-funnel-dropoff-analysis",
    "056-inventory-forecast",
    "057-interruption-resume",
    "058-multiday-project-state",
    "059-event-update-replan",
    "060-task-cancellation-cleanup",
    "061-periodic-status-rollup",
    "082-compose-config-repair",
    "083-monorepo-interface-repair",
    "084-js-state-type-bug",
    "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback",
    "087-cli-parser-bug-tests",
    "088-api-contract-mock-client-compat",
    "089-ab-test-caveat-analysis",
    "090-timeseries-anomaly-attribution",
    "091-financial-close-reconciliation",
    "092-schema-drift-audit",
    "093-jsonl-sessionization-analysis",
    "094-metric-definition-migration-diff",
    "103-policy-update-replan-diff",
    "104-async-ops-window-rollup",
    "105-partial-batch-resume-ledger",
    "106-release-approval-gate-plan",
]
DEFAULT_HARNESS = "prime-agent-gpt-5.4-medium"
EXPECTED_PROVIDER = "openai-codex"
EXPECTED_MODEL = "gpt-5.4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_result(results_dir: Path, harness_id: str, task_id: str) -> Path | None:
    matches = sorted((results_dir / harness_id).glob(f"*/{task_id}.json"))
    if len(matches) > 1:
        raise RuntimeError(f"multiple results found for {task_id}: {matches}")
    return matches[0] if matches else None


def validate_result(path: Path, task_id: str) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    usage = data.get("usage_summary") or {}
    oracle = data.get("oracle_result") or {}
    score = oracle.get("outcome_score", oracle.get("score"))
    errors: list[str] = []
    if data.get("task_id") != task_id:
        errors.append(f"task_id={data.get('task_id')!r}")
    if not (data.get("adapter_result") or {}).get("ok"):
        errors.append("adapter_result.ok is not true")
    if usage.get("providers") != [EXPECTED_PROVIDER]:
        errors.append(f"providers={usage.get('providers')!r}")
    if usage.get("models") != [EXPECTED_MODEL]:
        errors.append(f"models={usage.get('models')!r}")
    if not isinstance(score, (int, float)):
        errors.append("oracle outcome score missing")
    rubric = (data.get("scoring") or {}).get("rubric") or {}
    if not rubric.get("skipped"):
        errors.append("process rubric was not skipped")
    dependency_markers = ("No module named pytest", "No module named 'pytest'", "No module named 'yaml'")
    evidence = json.dumps(oracle, ensure_ascii=False)
    for adapter_round in data.get("adapter_results") or []:
        stdout_path = (adapter_round.get("metadata") or {}).get("stdout_log_file")
        if stdout_path and Path(stdout_path).is_file():
            evidence += Path(stdout_path).read_text(encoding="utf-8", errors="replace")
    found_markers = [marker for marker in dependency_markers if marker in evidence]
    if found_markers:
        errors.append(f"dependency failure in oracle/trajectory: {found_markers}")
    if errors:
        raise RuntimeError(f"invalid result {path}: {'; '.join(errors)}")
    return {
        "task_id": task_id,
        "result_file": str(path),
        "outcome_score": float(score),
        "elapsed_sec": data.get("elapsed_sec"),
        "total_tokens": usage.get("total_tokens"),
        "request_count": usage.get("request_count"),
        "sandbox": data.get("sandbox"),
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", default=DEFAULT_HARNESS)
    parser.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="allow a non-identical reproduction with a Prime Agent CLI version other than 0.7.2",
    )
    parser.add_argument("--manifest", type=Path, default=Path("reports/prime-agent-gpt-5.4-subset-manifest.json"))
    args = parser.parse_args()

    try:
        prime_agent_version = require_historical_prime_agent_version(
            allow_mismatch=args.allow_version_mismatch
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    app = load_app_config()
    missing_task_dirs = [task_id for task_id in SUBSET_TASKS if not (app.tasks_dir / task_id / "task.yaml").is_file()]
    if missing_task_dirs:
        raise SystemExit(f"frozen subset no longer matches checkout: {missing_task_dirs}")

    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join([str(Path("src").resolve()), env.get("PYTHONPATH", "")])
    env["HARNESSBENCH_SKIP_PROCESS_GRADE"] = "1"
    env["HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM"] = "1"
    # Prime's tools execute on the same host/network namespace as task hooks,
    # so loopback is preferable to exposing mock fixtures through a tunnel.
    env.setdefault("HARNESSBENCH_PUBLIC_URL_TEMPLATE", "{local_url}")

    manifest: dict[str, Any] = {
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "harness": args.harness,
        "provider": EXPECTED_PROVIDER,
        "model": EXPECTED_MODEL,
        "thinking": "medium",
        "expected_prime_agent_version": EXPECTED_PRIME_AGENT_VERSION,
        "prime_agent_version": prime_agent_version,
        "selection_rule": {"task_classes": SUBSET_CLASSES, "task_count": len(SUBSET_TASKS)},
        "tasks": SUBSET_TASKS,
        "results": [],
        "status": "running",
    }
    write_manifest(args.manifest, manifest)

    for index, task_id in enumerate(SUBSET_TASKS, start=1):
        existing = find_result(app.results_dir, args.harness, task_id)
        if existing is not None:
            record = validate_result(existing, task_id)
            record["run_status"] = "preexisting_valid"
            print(f"[subset {index}/{len(SUBSET_TASKS)}] {task_id}: using valid existing result", flush=True)
        else:
            print(f"[subset {index}/{len(SUBSET_TASKS)}] {task_id}: running", flush=True)
            command = [
                sys.executable,
                "-m",
                "harnessbench.cli",
                "run-task",
                "--task",
                task_id,
                "--harness",
                args.harness,
                "--mode",
                "live",
            ]
            started = utc_now()
            completed = subprocess.run(command, env=env, stdin=subprocess.DEVNULL)
            result_path = find_result(app.results_dir, args.harness, task_id)
            if completed.returncode != 0 or result_path is None:
                failure = {
                    "task_id": task_id,
                    "run_status": "command_failed",
                    "started_at": started,
                    "finished_at": utc_now(),
                    "returncode": completed.returncode,
                    "result_file": str(result_path) if result_path else None,
                }
                manifest["results"].append(failure)
                manifest["status"] = "stopped_on_failure"
                manifest["updated_at"] = utc_now()
                write_manifest(args.manifest, manifest)
                print(f"[subset] stopping after failure on {task_id}; rerun this script after correcting it", file=sys.stderr)
                return 1
            record = validate_result(result_path, task_id)
            record.update({"run_status": "completed", "started_at": started, "finished_at": utc_now()})

        manifest["results"].append(record)
        manifest["updated_at"] = utc_now()
        write_manifest(args.manifest, manifest)

    manifest["status"] = "complete"
    manifest["finished_at"] = utc_now()
    manifest["updated_at"] = utc_now()
    write_manifest(args.manifest, manifest)
    print(f"[subset] complete: {len(SUBSET_TASKS)} validated results; manifest={args.manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
