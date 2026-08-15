#!/usr/bin/env python3
"""Run the complete Harness-Bench suite with Hermes Agent sequentially and resumably."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnessbench.config import load_app_config, load_model_config


DEFAULT_HARNESS = "hermes-gpt-5.4-medium"
EXPECTED_PROVIDER = "openai-codex"
EXPECTED_MODEL = "gpt-5.4"
EXPECTED_TASK_COUNT = 106
EXPECTED_HERMES_VERSION = "0.20.1"
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
        errors.append(f"model_id={data.get('model_id')!r}; refusing non-Hermes/cross-harness reuse")
    if not (data.get("adapter_result") or {}).get("ok"):
        errors.append("adapter_result.ok is not true")
    if usage.get("providers") != [EXPECTED_PROVIDER]:
        errors.append(f"providers={usage.get('providers')!r}")
    if usage.get("models") != [EXPECTED_MODEL]:
        errors.append(f"models={usage.get('models')!r}")
    if usage.get("source") != "hermes_sqlite":
        errors.append(f"usage source={usage.get('source')!r}")
    if usage.get("billing_mode") != "subscription_included":
        errors.append(f"billing_mode={usage.get('billing_mode')!r}")
    if not isinstance(score, (int, float)):
        errors.append("oracle outcome score missing")
    rubric = (data.get("scoring") or {}).get("rubric") or {}
    if not rubric.get("skipped"):
        errors.append("process rubric was not skipped")
    if (data.get("scoring") or {}).get("proxy_trace_error"):
        errors.append(f"proxy_trace_error={(data.get('scoring') or {}).get('proxy_trace_error')!r}")

    evidence = json.dumps(oracle, ensure_ascii=False)
    adapter_rounds = data.get("adapter_results") or []
    if not adapter_rounds:
        errors.append("result has no adapter rounds")
    for adapter_round in adapter_rounds:
        metadata = adapter_round.get("metadata") or {}
        if not adapter_round.get("ok"):
            errors.append("adapter round is not successful")
        version = str(metadata.get("hermes_version") or "")
        if EXPECTED_HERMES_VERSION not in version:
            errors.append(f"unexpected Hermes version: {version!r}")
        if metadata.get("provider") != EXPECTED_PROVIDER or metadata.get("model") != EXPECTED_MODEL:
            errors.append("adapter round did not pin openai-codex/gpt-5.4")
        if metadata.get("reasoning") != "medium":
            errors.append(f"reasoning={metadata.get('reasoning')!r}")
        if metadata.get("timed_out"):
            errors.append("adapter round timed out")
        native = metadata.get("native_session") or {}
        if not metadata.get("hermes_session_id"):
            errors.append("adapter round lacks a native Hermes session")
        if int(native.get("assistant_message_count", 0) or 0) < 1:
            errors.append("native Hermes session has no assistant message")
        if native.get("billing_mode") != "subscription_included":
            errors.append(f"native billing_mode={native.get('billing_mode')!r}")
        trace = metadata.get("synthetic_trace") or {}
        if int(trace.get("response_count", 0) or 0) < 1:
            errors.append("adapter round has no retained native proxy responses")
        if trace.get("final_finish_reason") != "stop":
            errors.append(f"final native finish_reason={trace.get('final_finish_reason')!r}")
        if not metadata.get("staged_auth_removed"):
            errors.append("staged Hermes credential was not removed")
        for log_key in ("stdout_log_file", "stderr_log_file"):
            log_path = metadata.get(log_key)
            if not log_path or not Path(log_path).is_file():
                errors.append(f"missing native Hermes {log_key}")
            else:
                evidence += Path(log_path).read_text(encoding="utf-8", errors="replace")
    found_markers = [marker for marker in DEPENDENCY_MARKERS if marker in evidence]
    if found_markers:
        errors.append(f"dependency failure in oracle/trajectory: {found_markers}")

    sandbox_value = data.get("sandbox")
    if sandbox_value and (Path(sandbox_value) / ".hermes" / "auth.json").exists():
        errors.append("staged Hermes credential was not removed")
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
    parser.add_argument("--manifest", type=Path, default=Path("reports/hermes-gpt-5.4-medium-full-manifest.json"))
    parser.add_argument("--preflight-only", action="store_true", help="validate existing results without running tasks")
    args = parser.parse_args()

    app = load_app_config()
    model_configs = load_model_config()
    model_cfg = model_configs.get(args.harness)
    if not isinstance(model_cfg, dict) or model_cfg.get("adapter") != "hermes_agent":
        raise SystemExit(f"harness {args.harness!r} is not configured with the Hermes adapter")
    if model_cfg.get("provider") != EXPECTED_PROVIDER or model_cfg.get("model") != EXPECTED_MODEL:
        raise SystemExit(f"harness {args.harness!r} must pin {EXPECTED_PROVIDER}/{EXPECTED_MODEL}")

    source_auth = Path(os.path.expanduser(str(model_cfg.get("user_auth") or "~/.hermes/auth.json")))
    if not source_auth.is_file():
        raise SystemExit(f"Hermes OAuth source is missing: {source_auth}")
    try:
        auth_data = json.loads(source_auth.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Hermes OAuth source is invalid: {exc}")
    provider_state = (auth_data.get("providers") or {}).get(EXPECTED_PROVIDER) if isinstance(auth_data, dict) else None
    tokens = provider_state.get("tokens") if isinstance(provider_state, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise SystemExit(f"Hermes {EXPECTED_PROVIDER} OAuth credentials are incomplete")
    command_name = str(model_cfg.get("command") or "hermes")
    version_check = subprocess.run(
        [command_name, "--version"], text=True, capture_output=True, timeout=10, check=False,
    )
    if version_check.returncode != 0:
        raise SystemExit(f"Hermes command preflight failed: {version_check.stderr.strip()}")
    print(f"[full] Hermes auth staged source ready; {version_check.stdout.splitlines()[0]}", flush=True)

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
    # Hermes tools and task hooks share this host/network namespace.
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
