#!/usr/bin/env python3
"""Run the complete Harness-Bench suite with Pi sequentially and resumably."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnessbench.config import load_app_config, load_model_config


DEFAULT_HARNESS = "pi-gpt-5.4-medium"
EXPECTED_PROVIDER = "openai-codex"
EXPECTED_MODEL = "gpt-5.4"
EXPECTED_TASK_COUNT = 106
DEPENDENCY_MARKERS = (
    "No module named pytest",
    "No module named 'pytest'",
    "No module named yaml",
    "No module named 'yaml'",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_result(results_dir: Path, harness_id: str, task_id: str) -> Path | None:
    matches = sorted((results_dir / harness_id).glob(f"*/{task_id}.json"))
    if len(matches) > 1:
        raise RuntimeError(f"multiple results found for {task_id}: {matches}")
    return matches[0] if matches else None


def validate_result(path: Path, task_id: str, harness_id: str = DEFAULT_HARNESS) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    usage = data.get("usage_summary") or {}
    oracle = data.get("oracle_result") or {}
    score = oracle.get("outcome_score", oracle.get("score"))
    errors: list[str] = []
    if data.get("task_id") != task_id:
        errors.append(f"task_id={data.get('task_id')!r}")
    if data.get("model_id") != harness_id:
        errors.append(f"model_id={data.get('model_id')!r}; refusing non-Pi/cross-harness reuse")
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
    if (data.get("scoring") or {}).get("proxy_trace_error"):
        errors.append(f"proxy_trace_error={(data.get('scoring') or {}).get('proxy_trace_error')!r}")

    evidence = json.dumps(oracle, ensure_ascii=False)
    for adapter_round in data.get("adapter_results") or []:
        metadata = adapter_round.get("metadata") or {}
        if not metadata.get("pi_version"):
            errors.append("adapter round has no pi_version; refusing non-Pi result reuse")
        if not metadata.get("agent_end_seen"):
            errors.append("adapter round has no completed Pi agent_end event")
        if metadata.get("final_stop_reason") != "stop":
            errors.append(f"final_stop_reason={metadata.get('final_stop_reason')!r}")
        for log_key in ("stdout_log_file", "stderr_log_file"):
            log_path = metadata.get(log_key)
            if log_path and Path(log_path).is_file():
                evidence += Path(log_path).read_text(encoding="utf-8", errors="replace")
    found_markers = [marker for marker in DEPENDENCY_MARKERS if marker in evidence]
    if found_markers:
        errors.append(f"dependency failure in oracle/trajectory: {found_markers}")

    sandbox_value = data.get("sandbox")
    if sandbox_value and (Path(sandbox_value) / ".pi" / "agent" / "auth.json").exists():
        errors.append("staged Pi credential was not removed")
    if errors:
        raise RuntimeError(f"invalid result {path}: {'; '.join(errors)}")
    return {
        "task_id": task_id,
        "result_file": str(path),
        "outcome_score": float(score),
        "elapsed_sec": data.get("elapsed_sec"),
        "total_tokens": usage.get("total_tokens"),
        "request_count": usage.get("request_count"),
        "sandbox": sandbox_value,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", default=DEFAULT_HARNESS)
    parser.add_argument("--manifest", type=Path, default=Path("reports/pi-gpt-5.4-full-manifest.json"))
    parser.add_argument("--preflight-only", action="store_true", help="validate existing results without running tasks")
    args = parser.parse_args()

    app = load_app_config()
    model_configs = load_model_config()
    model_cfg = model_configs.get(args.harness)
    if not isinstance(model_cfg, dict) or model_cfg.get("adapter") != "pi":
        raise SystemExit(f"harness {args.harness!r} is not configured with the Pi adapter")
    if model_cfg.get("provider") != EXPECTED_PROVIDER or model_cfg.get("model") != EXPECTED_MODEL:
        raise SystemExit(f"harness {args.harness!r} must pin {EXPECTED_PROVIDER}/{EXPECTED_MODEL}")

    source_agent_dir = Path(os.path.expanduser(str(model_cfg.get("user_agent_dir") or "~/.pi/agent")))
    source_auth = source_agent_dir / "auth.json"
    if not source_auth.is_file():
        raise SystemExit(f"Pi OAuth source is missing: {source_auth}")
    command_name = str(model_cfg.get("command") or "pi")
    with tempfile.TemporaryDirectory(prefix="harnessbench-pi-auth-") as temporary_auth_dir:
        isolated_auth_dir = Path(temporary_auth_dir)
        isolated_auth = isolated_auth_dir / "auth.json"
        try:
            isolated_auth.symlink_to(source_auth)
        except OSError:
            shutil.copy2(source_auth, isolated_auth)
            isolated_auth.chmod(0o600)
        auth_env = os.environ.copy()
        auth_env["PI_CODING_AGENT_DIR"] = str(isolated_auth_dir)
        auth_env["PI_OFFLINE"] = "1"
        auth_env["PI_TELEMETRY"] = "0"
        auth_check = subprocess.run(
            [command_name, "auth", "check", "--provider", EXPECTED_PROVIDER, "--model", EXPECTED_MODEL,
             "--json", "--no-refresh"],
            text=True, capture_output=True, env=auth_env, check=False,
        )
    if auth_check.returncode != 0:
        detail = (auth_check.stdout.strip() or auth_check.stderr.strip() or "unknown auth error")
        raise SystemExit(f"Pi OAuth preflight failed: {detail}")
    print(f"[full] Pi auth ready: {auth_check.stdout.strip()}", flush=True)

    task_ids = sorted(path.name for path in app.tasks_dir.iterdir() if (path / "task.yaml").is_file())
    if len(task_ids) != EXPECTED_TASK_COUNT:
        raise SystemExit(f"expected {EXPECTED_TASK_COUNT} tasks at this benchmark revision, found {len(task_ids)}")

    # Validate every retained result before making a new model call. An invalid
    # existing result is never silently reused or overwritten.
    existing_records: dict[str, dict[str, Any]] = {}
    for task_id in task_ids:
        existing = find_result(app.results_dir, args.harness, task_id)
        if existing is not None:
            existing_records[task_id] = validate_result(existing, task_id, args.harness)
    print(
        f"[full] preflight: {len(task_ids)} tasks, {len(existing_records)} valid existing, "
        f"{len(task_ids) - len(existing_records)} pending",
        flush=True,
    )
    if args.preflight_only:
        return 0

    env = os.environ.copy()
    venv_bin = app.project_root / ".venv" / "bin"
    python_executable = venv_bin / "python"
    if not python_executable.is_file():
        raise SystemExit(f"benchmark interpreter not found: {python_executable}")
    # Intentionally use the .venv/bin path itself; do not resolve its Python symlink.
    env["PATH"] = os.pathsep.join([str(venv_bin), env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join([str(app.project_root / "src"), env.get("PYTHONPATH", "")])
    env["HARNESSBENCH_SKIP_PROCESS_GRADE"] = "1"
    env["HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM"] = "1"
    # Pi tools and task hooks share this host/network namespace.
    env.setdefault("HARNESSBENCH_PUBLIC_URL_TEMPLATE", "{local_url}")

    manifest: dict[str, Any] = {
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "harness": args.harness,
        "provider": EXPECTED_PROVIDER,
        "model": EXPECTED_MODEL,
        "thinking": "medium",
        "selection_rule": {"name": "complete_suite", "task_count": len(task_ids)},
        "tasks": task_ids,
        "valid_existing_count": len(existing_records),
        "pending_count": len(task_ids) - len(existing_records),
        "results": [],
        "status": "running",
    }
    write_manifest(args.manifest, manifest)

    for index, task_id in enumerate(task_ids, start=1):
        if task_id in existing_records:
            record = dict(existing_records[task_id])
            record["run_status"] = "preexisting_valid"
            print(f"[full {index}/{len(task_ids)}] {task_id}: using valid existing result", flush=True)
        else:
            print(f"[full {index}/{len(task_ids)}] {task_id}: running", flush=True)
            command = [
                str(python_executable),
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
                print(f"[full] stopping after failure on {task_id}; correct it, then rerun", file=sys.stderr)
                return 1
            try:
                record = validate_result(result_path, task_id, args.harness)
            except Exception as exc:
                manifest["results"].append({
                    "task_id": task_id,
                    "run_status": "validation_failed",
                    "started_at": started,
                    "finished_at": utc_now(),
                    "result_file": str(result_path),
                    "error": str(exc),
                })
                manifest["status"] = "stopped_on_failure"
                manifest["updated_at"] = utc_now()
                write_manifest(args.manifest, manifest)
                raise
            record.update({"run_status": "completed", "started_at": started, "finished_at": utc_now()})

        manifest["results"].append(record)
        manifest["updated_at"] = utc_now()
        write_manifest(args.manifest, manifest)

    manifest["status"] = "complete"
    manifest["finished_at"] = utc_now()
    manifest["updated_at"] = utc_now()
    write_manifest(args.manifest, manifest)
    print(f"[full] complete: {len(task_ids)} validated results; manifest={args.manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
