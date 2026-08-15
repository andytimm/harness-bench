#!/usr/bin/env python3
"""Summarize retained Harness-Bench result JSON without additional model calls."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

import yaml

from harnessbench.config import load_app_config
from run_prime_pilot import PILOT_TASKS


SUBSET_CLASSES = {
    "Software Engineering & Codebase Maintenance",
    "Data, BI & Finance Analytics",
    "Long-running Autonomy & State Adaptation",
}


def task_metadata(tasks_dir: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for task_dir in sorted(tasks_dir.iterdir()):
        task_file = task_dir / "task.yaml"
        if task_dir.is_dir() and task_file.is_file():
            data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
            metadata[task_dir.name] = dict(data)
    return metadata


def selected_tasks(selection: str, metadata: dict[str, dict[str, Any]]) -> list[str]:
    if selection == "pilot":
        return list(PILOT_TASKS)
    if selection == "subset":
        return [task_id for task_id, data in metadata.items() if data.get("class") in SUBSET_CLASSES]
    return list(metadata)


def find_result(results_dir: Path, harness: str, task_id: str) -> Path | None:
    candidates = sorted((results_dir / harness).glob(f"*/{task_id}.json"))
    if len(candidates) > 1:
        raise RuntimeError(f"multiple results for {task_id}: {candidates}")
    return candidates[0] if candidates else None


def score_from_result(data: dict[str, Any]) -> float | None:
    oracle = data.get("oracle_result") or {}
    raw = oracle.get("outcome_score", oracle.get("score"))
    return float(raw) if isinstance(raw, (int, float)) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="prime-agent-gpt-5.4-medium")
    parser.add_argument("--selection", choices=("pilot", "subset", "all"), default="pilot")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    app = load_app_config()
    metadata = task_metadata(app.tasks_dir)
    task_ids = selected_tasks(args.selection, metadata)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for task_id in task_ids:
        path = find_result(app.results_dir, args.harness, task_id)
        if path is None:
            missing.append(task_id)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        usage = data.get("usage_summary") or {}
        rows.append(
            {
                "task_id": task_id,
                "class": metadata.get(task_id, {}).get("class", ""),
                "outcome_score": score_from_result(data),
                "adapter_ok": bool((data.get("adapter_result") or {}).get("ok")),
                "elapsed_sec": data.get("elapsed_sec"),
                "model": data.get("api_model_label", ""),
                "requests": usage.get("request_count"),
                "input_tokens": usage.get("input_tokens"),
                "cache_read_tokens": usage.get("cache_read_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "result_file": str(path),
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.harness}-{args.selection}"
    csv_path = args.output_dir / f"{stem}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["task_id"])
        writer.writeheader()
        writer.writerows(rows)

    scores = [float(row["outcome_score"]) for row in rows if row["outcome_score"] is not None]
    elapsed = [float(row["elapsed_sec"]) for row in rows if row["elapsed_sec"] is not None]
    tokens = [int(row["total_tokens"]) for row in rows if row["total_tokens"] is not None]
    summary = {
        "harness": args.harness,
        "selection": args.selection,
        "expected_tasks": len(task_ids),
        "completed_tasks": len(rows),
        "missing_tasks": missing,
        "adapter_successes": sum(bool(row["adapter_ok"]) for row in rows),
        "mean_outcome_score": statistics.mean(scores) if scores else None,
        "perfect_outcomes": sum(score == 1.0 for score in scores),
        "wall_time_sec_sum": sum(elapsed),
        "wall_time_sec_mean": statistics.mean(elapsed) if elapsed else None,
        "wall_time_sec_median": statistics.median(elapsed) if elapsed else None,
        "total_tokens_sum": sum(tokens),
        "total_tokens_mean": statistics.mean(tokens) if tokens else None,
        "total_tokens_median": statistics.median(tokens) if tokens else None,
    }
    json_path = args.output_dir / f"{stem}.summary.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"rows: {csv_path}")
    print(f"summary: {json_path}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
