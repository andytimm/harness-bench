#!/usr/bin/env python3
"""Grade retained Harness-Bench trajectories post hoc with a fixed LLM judge."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnessbench.config import load_app_config
from harnessbench.extract_proxy_trace import extract_proxy_trace_incremental
from harnessbench.grading.process_grade import compute_scoring, load_rubric_prompts
from harnessbench.grading.rubric_llm import (
    append_workspace_out_text_excerpts_for_process_rubric,
    build_rubric_user_content_for_task,
)
from harnessbench.tasks import load_tasks, run_oracle

DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def proxy_trace_sha256(sandbox: Path) -> str:
    root = sandbox / "usage-proxy"
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError(f"no retained proxy files under {root}")
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def process_request_provenance(task: Any, sandbox: Path, oracle_result: dict[str, Any]) -> dict[str, Any]:
    trace = extract_proxy_trace_incremental(sandbox / "usage-proxy")
    if trace.get("error"):
        raise RuntimeError(f"proxy trace error: {trace['error']}")
    full_payload = json.dumps(trace, ensure_ascii=False)
    payload = full_payload
    if len(payload) > 24000:
        payload = payload[:24000] + "\n...[truncated]"
    system, user, prompt_source = load_rubric_prompts(task, payload, Path(__file__).resolve().parents[1])
    user_content = build_rubric_user_content_for_task(task.task_id, user, sandbox / "workspace")
    user_content = append_workspace_out_text_excerpts_for_process_rubric(
        task.task_id,
        sandbox / "workspace",
        user_content,
        effective_outcome_llm_weight=float(oracle_result.get("outcome_llm_weight") or 0),
    )
    canonical = json.dumps(
        {"model": DEFAULT_MODEL, "temperature": 0.2, "max_tokens": 1500,
         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "trace_payload_chars": len(full_payload),
        "trace_payload_truncated": len(full_payload) > 24000,
        "rubric_prompt_source": prompt_source,
    }


def find_result(results_dir: Path, harness: str, task_id: str) -> Path:
    matches = sorted((results_dir / harness).glob(f"*/{task_id}.json"))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {harness}/{task_id} result, found {matches}")
    return matches[0]


def numeric_cost(value: Any) -> float:
    total = 0.0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "cost" and isinstance(item, (int, float)):
                total += float(item)
            elif key != "cost_details":
                total += numeric_cost(item)
    elif isinstance(value, list):
        total += sum(numeric_cost(item) for item in value)
    return total


def validate_scoring(
    scoring: dict[str, Any], expected_model: str, expected_provider: str = "Anthropic"
) -> None:
    rubric = scoring.get("rubric") or {}
    if rubric.get("skipped"):
        raise RuntimeError(f"rubric skipped: {rubric.get('reason')}")
    if rubric.get("parse_error"):
        raise RuntimeError("rubric response was not valid JSON")
    if rubric.get("rubric_model") != expected_model:
        raise RuntimeError(f"rubric_model={rubric.get('rubric_model')!r}")
    if rubric.get("response_provider") != expected_provider:
        raise RuntimeError(f"response_provider={rubric.get('response_provider')!r}")
    if rubric.get("response_model") not in (None, expected_model):
        raise RuntimeError(f"response_model={rubric.get('response_model')!r}")
    if rubric.get("finish_reason") not in (None, "stop"):
        raise RuntimeError(f"finish_reason={rubric.get('finish_reason')!r}")
    parsed = rubric.get("parsed_response")
    if not isinstance(parsed, dict):
        raw = rubric.get("raw_content")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, dict):
        raise RuntimeError("full parsed rubric response is unavailable")
    allowed = {"scores", "security_gate", "notes", "total"}
    if not set(parsed).issubset(allowed) or not {"scores", "security_gate", "notes"}.issubset(parsed):
        raise RuntimeError(f"unexpected rubric keys={sorted(parsed)}")
    scores = parsed.get("scores")
    required = {"tool_use_appropriate", "consistency", "robustness"}
    if not isinstance(scores, dict) or set(scores) != required:
        raise RuntimeError(f"invalid process score keys={sorted(scores) if isinstance(scores, dict) else scores!r}")
    for key in required:
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise RuntimeError(f"invalid {key}={value!r}")
    gate = parsed.get("security_gate")
    if isinstance(gate, bool):
        gate = int(gate)
    if gate not in (0, 1):
        raise RuntimeError(f"invalid explicit security_gate={gate!r}")
    if not isinstance(parsed.get("notes"), str):
        raise RuntimeError("rubric notes must be a string")
    if not isinstance(scoring.get("process_score"), (int, float)):
        raise RuntimeError("process_score missing")
    if scoring.get("security_score") not in (0, 0.0, 1, 1.0):
        raise RuntimeError(f"invalid security_score={scoring.get('security_score')!r}")
    usage = rubric.get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("cost"), (int, float)):
        raise RuntimeError("judge usage/cost metadata is missing")

def load_credentials(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    if not values.get("RUBRIC_API_KEY"):
        raise RuntimeError(f"RUBRIC_API_KEY is missing from {path}")
    values.setdefault("RUBRIC_BASE_URL", DEFAULT_BASE_URL)
    values.setdefault("RUBRIC_MODEL", DEFAULT_MODEL)
    values.setdefault("RUBRIC_VISION_MODEL", values["RUBRIC_MODEL"])
    return values


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", action="append", required=True)
    parser.add_argument("--task", action="append", help="grade only selected task id(s)")
    parser.add_argument("--credential-file", type=Path, default=Path("~/.config/harnessbench/rubric.env"))
    parser.add_argument("--output-root", type=Path, default=Path("evaluation/process-grades/claude-sonnet-4.6"))
    parser.add_argument("--manifest", type=Path, default=Path("reports/process-grade-claude-sonnet-4.6-manifest.json"))
    parser.add_argument("--max-cost-usd", type=float, default=20.0)
    parser.add_argument("--max-new", type=int)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    credentials = load_credentials(args.credential_file.expanduser())
    expected_model = credentials["RUBRIC_MODEL"]
    if expected_model != DEFAULT_MODEL:
        raise SystemExit(f"judge must be pinned to {DEFAULT_MODEL}, got {expected_model}")
    os.environ.update(credentials)
    os.environ.pop("HARNESSBENCH_SKIP_PROCESS_GRADE", None)
    os.environ.pop("HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM", None)
    os.environ.setdefault("HARNESSBENCH_RUBRIC_MAX_TOKENS", "1500")

    app = load_app_config()
    tasks = load_tasks(app.tasks_dir)
    task_ids = args.task or sorted(tasks)
    unknown = [task_id for task_id in task_ids if task_id not in tasks]
    if unknown:
        raise SystemExit(f"unknown tasks: {unknown}")

    jobs: list[dict[str, Any]] = []
    existing_cost = 0.0
    for harness in args.harness:
        for task_id in task_ids:
            source = find_result(app.results_dir, harness, task_id)
            data = json.loads(source.read_text(encoding="utf-8"))
            if data.get("model_id") != harness or not (data.get("adapter_result") or {}).get("ok"):
                raise RuntimeError(f"invalid source result {source}")
            sandbox = Path(data["sandbox"])
            workspace = sandbox / "workspace"
            if not workspace.is_dir():
                raise RuntimeError(f"retained workspace is absent: {workspace}")
            trace_hash = proxy_trace_sha256(sandbox)
            workspace_hash = sha256_tree(workspace)
            request_provenance = process_request_provenance(
                tasks[task_id], sandbox, data.get("oracle_result") or {}
            )
            output = args.output_root / harness / f"{task_id}.json"
            source_hash = sha256_file(source)
            existing = None
            if output.is_file():
                existing = json.loads(output.read_text(encoding="utf-8"))
                if (
                    existing.get("status") != "completed"
                    or existing.get("source_result_sha256") != source_hash
                    or existing.get("proxy_trace_sha256") != trace_hash
                    or existing.get("judge_model") != expected_model
                    or existing.get("workspace_sha256") not in (None, workspace_hash)
                    or existing.get("process_request", {}).get("request_sha256") not in (
                        None, request_provenance["request_sha256"]
                    )
                ):
                    raise RuntimeError(f"existing grade has mismatched provenance: {output}")
                validate_scoring(existing["scoring"], expected_model)
                existing_cost += float(existing.get("reported_cost_usd") or 0)
            jobs.append({
                "harness": harness, "task_id": task_id, "task": tasks[task_id],
                "source": source, "source_hash": source_hash, "source_data": data,
                "sandbox": sandbox, "workspace": workspace, "trace_hash": trace_hash,
                "workspace_hash": workspace_hash, "request_provenance": request_provenance,
                "output": output, "existing": existing,
            })

    pending = [job for job in jobs if job["existing"] is None]
    print(
        f"[grade] preflight: {len(jobs)} jobs, {len(jobs)-len(pending)} valid existing, "
        f"{len(pending)} pending; retained reported cost=${existing_cost:.4f}", flush=True,
    )
    if args.preflight_only:
        return 0

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "judge_model": expected_model,
        "judge_base_url": credentials["RUBRIC_BASE_URL"],
        "harnesses": args.harness,
        "tasks": task_ids,
        "max_cost_usd": args.max_cost_usd,
        "results": [],
        "status": "running",
    }
    write_json(args.manifest, manifest)
    new_count = 0
    running_cost = existing_cost
    for index, job in enumerate(jobs, start=1):
        if job["existing"] is not None:
            manifest["results"].append({
                "harness": job["harness"], "task_id": job["task_id"],
                "status": "preexisting_valid", "grade_file": str(job["output"]),
            })
            continue
        if args.max_new is not None and new_count >= args.max_new:
            manifest["status"] = "stopped_at_max_new"
            break
        oracle_result = dict(job["source_data"].get("oracle_result") or {})
        weight = float(oracle_result.get("outcome_llm_weight") or 0)
        reservation = 0.10 + (0.25 if weight > 0 and not isinstance(oracle_result.get("quality"), (int, float)) else 0)
        if running_cost + reservation > args.max_cost_usd:
            manifest["status"] = "stopped_at_cost_limit"
            break

        print(f"[grade {index}/{len(jobs)}] {job['harness']} {job['task_id']}", flush=True)
        if weight > 0 and not isinstance(oracle_result.get("quality"), (int, float)):
            oracle_result = run_oracle(job["task"], job["workspace"])
            if not isinstance(oracle_result.get("quality"), (int, float)):
                raise RuntimeError(f"quality judgment missing for {job['harness']} {job['task_id']}")
        scoring = compute_scoring(job["task"], job["sandbox"], oracle_result)
        validate_scoring(scoring, expected_model)
        reported_cost = numeric_cost({"oracle_result": oracle_result, "scoring": scoring})
        if reported_cost <= 0:
            reported_cost = reservation
            cost_source = "conservative_reservation_missing_provider_cost"
        else:
            cost_source = "openrouter_usage_cost"
        record = {
            "schema_version": SCHEMA_VERSION,
            "status": "completed",
            "created_at": utc_now(),
            "harness": job["harness"],
            "task_id": job["task_id"],
            "judge_model": expected_model,
            "judge_base_url": credentials["RUBRIC_BASE_URL"],
            "source_result_file": str(job["source"]),
            "source_result_sha256": job["source_hash"],
            "proxy_trace_sha256": job["trace_hash"],
            "workspace_sha256": job["workspace_hash"],
            "process_request": job["request_provenance"],
            "oracle_result": oracle_result,
            "scoring": scoring,
            "reported_cost_usd": reported_cost,
            "cost_source": cost_source,
        }
        write_json(job["output"], record)
        running_cost += reported_cost
        new_count += 1
        manifest["results"].append({
            "harness": job["harness"], "task_id": job["task_id"],
            "status": "completed", "grade_file": str(job["output"]),
            "reported_cost_usd": reported_cost,
        })
        manifest["reported_cost_usd"] = running_cost
        manifest["updated_at"] = utc_now()
        write_json(args.manifest, manifest)

    if manifest["status"] == "running":
        manifest["status"] = "complete"
    manifest["finished_at"] = utc_now()
    manifest["updated_at"] = utc_now()
    manifest["reported_cost_usd"] = running_cost
    write_json(args.manifest, manifest)
    print(f"[grade] {manifest['status']}; new={new_count}; reported cost=${running_cost:.4f}", flush=True)
    return 0 if manifest["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
