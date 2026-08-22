#!/usr/bin/env python3
"""Build the audited, shareable Harness-Bench comparison.

The report deliberately excludes the contaminated original Hermes run and any
incomplete Claude results. Headline quality uses uniformly graded full-trace-v2
combined scores while preserving outcome, process, and security components.
"""
from __future__ import annotations

import argparse
import csv
import html
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, LogFormatterMathtext, NullFormatter
import numpy as np
import yaml

INK = "#111111"
MUTED = "#6F716D"
PAPER = "#FAFAF8"
GRID = "#DADBD5"
ACCENT = "#85ED75"
COLORS = {
    "prime": ACCENT,
    "pi": "#292A28",
    "codex": "#667C91",
    "hermes_default": "#C6A96A",
    "hermes_focused": "#B97A36",
    "hermes_aggressive": "#2A9586",
}
TOPIC_COLORS = {
    "Workspace, Tool Use & Multimodal Operations": "#4C78A8",
    "Office & Business Communication": "#F28E2B",
    "Long-running Autonomy & State Adaptation": "#59A14F",
    "Software Engineering & Codebase Maintenance": "#E15759",
    "Knowledge, Evidence & Retrieval": "#B279A2",
    "SRE, DevOps & Release Ops": "#76B7B2",
    "Data, BI & Finance Analytics": "#EDC948",
    "Vertical Professional Workflows": "#9C755F",
}
META = {
    "prime": ("Prime Agent", "GPT-5.4 medium"),
    "pi": ("Pi", "GPT-5.4 medium"),
    "codex": ("Codex CLI 0.139", "GPT-5.4 medium"),
    "hermes_default": ("Hermes — default", "GPT-5.4 medium"),
    "hermes_focused": ("Hermes — focused*", "GPT-5.4 medium"),
    "hermes_aggressive": ("Hermes — aggressive*", "GPT-5.4 medium"),
}
ALL_ORDER = ["prime", "pi", "hermes_default", "codex", "hermes_aggressive", "hermes_focused"]
MAIN_ORDER = ["prime", "pi", "hermes_default", "codex"]


def read_json(path: Path):
    return json.loads(path.read_text())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_provenance(repo: Path, runs: Path) -> dict:
    leak_tasks = ("037-policy-clause-retrieval", "046-performance-regression",
                  "055-funnel-dropoff-analysis", "079-smallfile-batch-reject-ledger")
    leak_paths = [repo / "data_try6/results/hermes-gpt-5.4-medium/gpt-5.4" / f"{task}.json" for task in leak_tasks]
    for path in leak_paths:
        text = path.read_text()
        if "ground_truth" not in text or "/tasks/" not in text:
            raise RuntimeError(f"historical Hermes contamination evidence missing expected markers: {path}")
    claude_first = ("001-file", "003-browser", "005-email-triage", "007-session-memory")
    claude_second = ("009-git-pr-merge", "011-code-debug", "013-image-edit", "015-security-injection-defense", "017-db-doc-consistency")
    claude_roots = [(Path.home() / "harnessbench-claude-opus46-odd-53-e529f64", claude_first),
                    (Path.home() / "harnessbench-claude-opus46-odd-window2-e529f64", claude_second)]
    claude_pilot_paths = []
    for root, tasks in claude_roots:
        for task in tasks:
            matches = list((root / "control-plane/results").rglob(f"{task}.json"))
            if len(matches) != 1:
                raise RuntimeError(f"expected one Claude pilot result for {task}, got {matches}")
            text = matches[0].read_text()
            if not any(marker in text for marker in ("E2BIG", "don't-ask", "denied by policy", "blanket-denied")):
                raise RuntimeError(f"Claude pilot result lacks expected systemic tool-failure marker: {matches[0]}")
            claude_pilot_paths.append(matches[0])
    groups = {
        "task_metadata": list((repo / "tasks").glob("*/task.yaml")),
        "excluded_original_hermes_evidence": leak_paths,
        "invalid_claude_pilot_evidence": claude_pilot_paths,
        "prime": list((repo / "data_try6/results/prime-agent-gpt-5.4-medium/gpt-5.4").glob("*.json")) + [repo / "reports/prime-agent-gpt-5.4-full-manifest.json"],
        "pi": list((repo / "data_try6/results/pi-gpt-5.4-medium/gpt-5.4").glob("*.json")) + [repo / "reports/pi-gpt-5.4-full-manifest.json"],
        "codex": [p for p in (repo / "data_try6/results/codex-gpt-5.4-medium-current-0.139/gpt-5.4").glob("*.json") if p.stem != "023-web-form-extraction"] + [repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/results/codex-gpt-5.4-medium-current-0.139/gpt-5.4/023-web-form-extraction.json", repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/full/manifest.json", repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/manifest.json", repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/CORRECTION_ATTEMPT_AUDIT.json", Path.home() / ".harnessbench/evaluation-gates/codex-gpt-5.4-medium-current-0.139/AUDIT.json"],
        "hermes_default": list((runs / "post-codex-contained-default-hermes-106/results/rerun/post-codex-hermes-contained-default-106/gpt-5.4").glob("*.json")) + [runs / "post-codex-contained-default-hermes-106/POST_RUN_AUDIT.json", runs / "post-codex-contained-default-hermes-106/INDEPENDENT_REVIEW.json"],
        "hermes_fixed_common": [runs / "post-codex-hermes-fixed-106/POST_RUN_AUDIT.json"],
        "hermes_focused": list((runs / "post-codex-hermes-fixed-106/profiles/focused-with-skills/results/ablation/post-codex-hermes-fixed-profiles-106/focused-with-skills/gpt-5.4").glob("*.json")),
        "hermes_aggressive": list((runs / "post-codex-hermes-fixed-106/profiles/aggressive-no-skills/results/ablation/post-codex-hermes-fixed-profiles-106/aggressive-no-skills/gpt-5.4").glob("*.json")) + list((runs / "post-codex-hermes-watchdog-corrections-6/results/correction/post-codex-hermes-fixed-aggressive-watchdog-v1/gpt-5.4").glob("*.json")) + [runs / "post-codex-hermes-watchdog-corrections-6/CORRECTION_AUDIT.json"],
        "process_prime_pi": list((repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2/prime-agent-gpt-5.4-medium").glob("*.json")) + list((repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2/pi-gpt-5.4-medium").glob("*.json")) + [repo / "reports/process-grade-claude-sonnet-4.6-full-trace-v2-manifest.json"],
        "process_corrected_codex_hermes": list((repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2-corrected/codex-gpt-5.4-medium-current-0.139").glob("*.json")) + list((repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2-corrected/rerun/post-codex-hermes-contained-default-106").glob("*.json")) + [repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2-corrected-manifest.json"],
    }
    entries = []
    for dataset, paths in groups.items():
        for path in sorted(set(paths)):
            if not path.is_file():
                raise RuntimeError(f"missing provenance input: {path}")
            try:
                logical = "repo/" + str(path.relative_to(repo))
            except ValueError:
                try: logical = "runs/" + str(path.relative_to(runs))
                except ValueError:
                    try: logical = "home/" + str(path.relative_to(Path.home()))
                    except ValueError: logical = "external/" + path.name
            entries.append({"dataset": dataset, "path": logical, "sha256": sha256_file(path)})
    return {"schema": 2, "headline_metric": "mean taskwise scoring.combined_score",
            "score_formula": "combined_score = Python round(outcome_score × unrounded mean(tool_use_appropriate, consistency, robustness) × security_score, 4); process_score/process_effective separately round that mean to 4 decimals",
            "process_grade_contract": {"judge_model": "anthropic/claude-sonnet-4.6", "trace_policy": "full_valid_json", "max_payload_chars": 1000000},
            "task_count_per_option": 106, "input_count": len(entries), "inputs": entries,
            "composition": {"codex": "105 full-manifest entries plus correction-023-loopback-network task 023", "hermes_aggressive": "100 fixed-root results plus six watchdog-correction results (091,094,096,097,099,106)", "process_grades": "106 source-bound full-trace-v2 grades for each headline option; legacy-prefix and original-Hermes grades excluded"},
            "exclusions": {"original_hermes": {"tasks": list(leak_tasks), "finding": "retained result records contain ground_truth and benchmark task-root access markers"},
                           "claude_pilot": {"tasks": list(claude_first + claude_second), "mean_score": 0.09333333333333332, "finding": "all 51 Bash calls failed before shell startup with E2BIG; all 30 Write and 8 Edit calls were policy-denied; no mutation channel, so not a model-quality baseline"}}}


def result_row(task_id: str, result: dict, harness: str, topic: str, provenance: str,
               process_grade: dict | None = None) -> dict:
    usage = result["usage_summary"]
    execution_scoring = result["scoring"]
    # Standardize input across APIs. Some providers report cache reads inside
    # input_tokens (Codex), while others report them separately. In the public
    # table, Input means non-cache-read input and the components satisfy:
    # total = input + cache read + output. Cache creation, if present, is fresh
    # input rather than a cache read.
    reported_input = int(usage.get("input_tokens", 0))
    cache_read = int(usage.get("cache_read_tokens", 0))
    cache_write = int(usage.get("cache_write_tokens", 0))
    output = int(usage.get("output_tokens", 0))
    total = int(usage["total_tokens"])
    standardized_input = total - cache_read - output
    if standardized_input < 0 or standardized_input + cache_read + output != total:
        raise RuntimeError(f"invalid standardized token identity for {harness}/{task_id}: {usage}")
    execution_outcome = float(execution_scoring["outcome_score"])
    grade_scoring = process_grade["scoring"] if process_grade is not None else None
    rubric_scores = grade_scoring["rubric"]["scores"] if grade_scoring else None
    outcome = float(grade_scoring["outcome_score"]) if grade_scoring else execution_outcome
    return {
        "harness_id": harness,
        "harness": META[harness][0],
        "model": META[harness][1],
        "task_id": task_id,
        "topic": topic,
        "execution_outcome_score": execution_outcome,
        "outcome_score": outcome,
        "process_score": float(grade_scoring["process_score"]) if grade_scoring else None,
        "process_tool_use_appropriate": float(rubric_scores["tool_use_appropriate"]) if rubric_scores else None,
        "process_consistency": float(rubric_scores["consistency"]) if rubric_scores else None,
        "process_robustness": float(rubric_scores["robustness"]) if rubric_scores else None,
        "process_effective_unrounded": process_grade["_process_effective_unrounded"] if process_grade else None,
        "security_score": float(grade_scoring["security_score"]) if grade_scoring else None,
        "combined_score": float(grade_scoring["combined_score"]) if grade_scoring else None,
        "process_grade_sha256": process_grade["_grade_sha256"] if process_grade else "",
        "process_source_result_sha256": process_grade["source_result_sha256"] if process_grade else "",
        "process_proxy_trace_sha256": process_grade["proxy_trace_sha256"] if process_grade else "",
        "process_workspace_sha256": process_grade["workspace_sha256"] if process_grade else "",
        "process_request_sha256": process_grade["process_request"]["request_sha256"] if process_grade else "",
        "process_grade_cost_usd": float(process_grade.get("reported_cost_usd") or 0) if process_grade else None,
        "elapsed_seconds": float(result["elapsed_sec"]),
        "calls": int(usage["request_count"]),
        "input_tokens": standardized_input,
        "reported_input_tokens": reported_input,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": output,
        "reasoning_tokens": int(usage.get("reasoning_tokens", 0)),
        "total_tokens": total,
        "provenance": provenance,
    }


def load_result_dir(path: Path) -> dict[str, dict]:
    files = sorted(path.glob("*.json"))
    rows = {read_json(p)["task_id"]: read_json(p) for p in files}
    if len(files) != 106 or len(rows) != 106:
        raise RuntimeError(f"expected 106 unique results at {path}, got {len(files)}/{len(rows)}")
    return rows


def load_process_grade_dir(path: Path, expected_harness: str) -> dict[str, dict]:
    files = sorted(path.glob("*.json"))
    records = {}
    for grade_file in files:
        record = read_json(grade_file)
        task_id = record.get("task_id")
        scoring = record.get("scoring") or {}
        request = record.get("process_request") or {}
        rubric = scoring.get("rubric") or {}
        values = [scoring.get(key) for key in ("outcome_score", "process_score", "security_score", "combined_score")]
        if (record.get("status") != "completed" or record.get("harness") != expected_harness or
                record.get("judge_model") != "anthropic/claude-sonnet-4.6" or
                record.get("trace_policy") != "full_valid_json" or record.get("max_payload_chars") != 1_000_000 or
                request.get("trace_payload_truncated") is not False or rubric.get("skipped") or rubric.get("parse_error") or
                rubric.get("rubric_model") != "anthropic/claude-sonnet-4.6" or
                rubric.get("response_provider") != "Anthropic" or rubric.get("finish_reason") not in (None, "stop") or
                set((rubric.get("scores") or {})) != {"tool_use_appropriate", "consistency", "robustness"} or
                not task_id or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values) or
                any(not 0 <= float(value) <= 1 for value in values) or
                not isinstance(scoring.get("process_effective"), (int, float)) or
                scoring.get("security_score") not in (0, 0.0, 1, 1.0) or
                len(str(record.get("source_result_sha256", ""))) != 64 or
                len(str(record.get("proxy_trace_sha256", ""))) != 64 or
                len(str(record.get("workspace_sha256", ""))) != 64 or
                len(str(request.get("request_sha256", ""))) != 64):
            raise RuntimeError(f"invalid full-trace-v2 process grade: {grade_file}")
        dimension_scores = rubric["scores"]
        # Match the evaluator exactly: explicit left-associative float addition
        # (Python 3.12+ built-in sum uses a different compensated algorithm).
        tool_use = float(dimension_scores["tool_use_appropriate"])
        consistency = float(dimension_scores["consistency"])
        robustness = float(dimension_scores["robustness"])
        process_unrounded = (tool_use + consistency + robustness) / 3.0
        if (abs(float(scoring["process_score"]) - round(process_unrounded, 4)) > 5e-10 or
                abs(float(scoring["process_effective"]) - round(process_unrounded, 4)) > 5e-10):
            raise RuntimeError(f"process-score rounding mismatch: {grade_file}")
        expected_combined = round(float(scoring["outcome_score"]) * process_unrounded * float(scoring["security_score"]), 4)
        if abs(float(scoring["combined_score"]) - expected_combined) > 5e-10:
            raise RuntimeError(f"combined-score formula mismatch: {grade_file}")
        record["_process_effective_unrounded"] = process_unrounded
        record["_grade_file"] = str(grade_file)
        record["_grade_sha256"] = sha256_file(grade_file)
        if task_id in records:
            raise RuntimeError(f"duplicate process grade for {expected_harness}/{task_id}")
        records[task_id] = record
    if len(files) != 106 or len(records) != 106:
        raise RuntimeError(f"expected 106 unique full-trace-v2 grades at {path}, got {len(files)}/{len(records)}")
    return records


def task_metadata_rows(repo: Path) -> list[dict]:
    rows = []
    for task_file in sorted((repo / "tasks").glob("*/task.yaml")):
        meta = yaml.safe_load(task_file.read_text())
        rows.append({"task_id": meta["task_id"], "title": meta["title"], "class": meta["class"],
                     "tags": "|".join(meta.get("tags", [])), "difficulty": meta.get("difficulty", "")})
    if len(rows) != 106:
        raise RuntimeError(f"expected 106 metadata rows, got {len(rows)}")
    return rows


def load_inputs(repo: Path, runs: Path) -> tuple[list[dict], dict[str, str]]:
    topics = {}
    for task_file in sorted((repo / "tasks").glob("*/task.yaml")):
        meta = yaml.safe_load(task_file.read_text())
        topics[meta["task_id"]] = meta["class"]
    if len(topics) != 106:
        raise RuntimeError(f"expected 106 task topics, got {len(topics)}")

    datasets: dict[str, tuple[dict[str, dict], str]] = {}
    datasets["prime"] = (
        load_result_dir(repo / "data_try6/results/prime-agent-gpt-5.4-medium/gpt-5.4"),
        "canonical Prime 106-task run",
    )
    datasets["pi"] = (
        load_result_dir(repo / "data_try6/results/pi-gpt-5.4-medium/gpt-5.4"),
        "canonical Pi 106-task run",
    )

    full = read_json(repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/full/manifest.json")["entries"]
    correction = read_json(repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/manifest.json")["entries"]
    codex = dict(full)
    codex["023-web-form-extraction"] = correction["023-web-form-extraction"]
    if len(codex) != 106 or any(x.get("status") != "succeeded" for x in codex.values()):
        raise RuntimeError("corrected Codex composition is not 106 successful tasks")
    datasets["codex"] = (codex, "105 initial Codex tasks + audited operational correction 2 for task 023")

    default_root = runs / "post-codex-contained-default-hermes-106"
    datasets["hermes_default"] = (
        load_result_dir(default_root / "results/rerun/post-codex-hermes-contained-default-106/gpt-5.4"),
        "contained benchmark-default Hermes rerun",
    )
    fixed_root = runs / "post-codex-hermes-fixed-106"
    focused = load_result_dir(fixed_root / "profiles/focused-with-skills/results/ablation/post-codex-hermes-fixed-profiles-106/focused-with-skills/gpt-5.4")
    aggressive = load_result_dir(fixed_root / "profiles/aggressive-no-skills/results/ablation/post-codex-hermes-fixed-profiles-106/aggressive-no-skills/gpt-5.4")
    corr_root = runs / "post-codex-hermes-watchdog-corrections-6/results/correction/post-codex-hermes-fixed-aggressive-watchdog-v1/gpt-5.4"
    corrections = {read_json(p)["task_id"]: read_json(p) for p in sorted(corr_root.glob("*.json"))}
    expected_corrections = {
        "091-financial-close-reconciliation", "094-metric-definition-migration-diff",
        "096-offline-knowledge-qa-insufficient-evidence", "097-research-claims-batch-evidence-audit",
        "099-privacy-dsar-intake-review", "106-release-approval-gate-plan",
    }
    if set(corrections) != expected_corrections:
        raise RuntimeError(f"unexpected Hermes correction set: {set(corrections)}")
    aggressive.update(corrections)
    datasets["hermes_focused"] = (focused, "post-hoc focused-with-skills ablation")
    datasets["hermes_aggressive"] = (aggressive, "post-hoc aggressive-no-skills ablation; six watchdog corrections")

    grade_root = repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2"
    corrected_grade_root = repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2-corrected"
    grade_specs = {
        "prime": (grade_root / "prime-agent-gpt-5.4-medium", "prime-agent-gpt-5.4-medium"),
        "pi": (grade_root / "pi-gpt-5.4-medium", "pi-gpt-5.4-medium"),
        "codex": (corrected_grade_root / "codex-gpt-5.4-medium-current-0.139", "codex-gpt-5.4-medium-current-0.139"),
        "hermes_default": (corrected_grade_root / "rerun/post-codex-hermes-contained-default-106", "rerun/post-codex-hermes-contained-default-106"),
    }
    process_grades = {harness: load_process_grade_dir(path, grade_harness)
                      for harness, (path, grade_harness) in grade_specs.items()}
    source_paths = {
        "prime": {p.stem: p for p in (repo / "data_try6/results/prime-agent-gpt-5.4-medium/gpt-5.4").glob("*.json")},
        "pi": {p.stem: p for p in (repo / "data_try6/results/pi-gpt-5.4-medium/gpt-5.4").glob("*.json")},
        "codex": {p.stem: p for p in (repo / "data_try6/results/codex-gpt-5.4-medium-current-0.139/gpt-5.4").glob("*.json")},
        "hermes_default": {p.stem: p for p in (default_root / "results/rerun/post-codex-hermes-contained-default-106/gpt-5.4").glob("*.json")},
    }
    source_paths["codex"]["023-web-form-extraction"] = repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/results/codex-gpt-5.4-medium-current-0.139/gpt-5.4/023-web-form-extraction.json"
    for harness in MAIN_ORDER:
        results = datasets[harness][0]
        if set(process_grades[harness]) != set(results) or set(source_paths[harness]) != set(results):
            raise RuntimeError(f"process-grade/source task mismatch for {harness}")
        for task_id, grade in process_grades[harness].items():
            source = source_paths[harness][task_id]
            if grade["source_result_sha256"] != sha256_file(source):
                raise RuntimeError(f"process grade is not bound to authoritative source: {harness}/{task_id}")
            oracle_workspace_raw = grade["oracle_result"].get("workspace")
            if oracle_workspace_raw and Path(oracle_workspace_raw).resolve() != (Path(results[task_id]["sandbox"]) / "workspace").resolve():
                raise RuntimeError(f"process grade workspace mismatch: {harness}/{task_id}")
            execution_outcome = float(results[task_id]["scoring"]["outcome_score"])
            graded_outcome = float(grade["scoring"]["outcome_score"])
            if abs(execution_outcome - graded_outcome) > 5e-10 and task_id not in {"008-image-recognize", "013-image-edit"}:
                raise RuntimeError(f"unexpected outcome change during process grading: {harness}/{task_id}")

    rows = []
    for harness, (results, provenance) in datasets.items():
        if set(results) != set(topics):
            raise RuntimeError(f"task mismatch for {harness}")
        grades = process_grades.get(harness, {})
        for task_id in sorted(results):
            rows.append(result_row(task_id, results[task_id], harness, topics[task_id], provenance, grades.get(task_id)))
    return rows, topics


def bootstrap_mean_ci(values, rng, draws=10000):
    values = np.asarray(values, dtype=float)
    samples = rng.choice(values, size=(draws, len(values)), replace=True).mean(axis=1)
    return tuple(np.quantile(samples, [0.025, 0.975]))


def aggregate(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_h = defaultdict(list)
    by_ht = defaultdict(list)
    for row in rows:
        by_h[row["harness_id"]].append(row)
        by_ht[(row["harness_id"], row["topic"])].append(row)
    rng = np.random.default_rng(20260816)

    def metric_summary(rs: list[dict], key: str, draws: int) -> tuple[float | None, float | None, float | None]:
        values = [row[key] for row in rs]
        if any(value is None for value in values):
            return None, None, None
        mean = float(np.mean(values))
        lo, hi = bootstrap_mean_ci(values, rng, draws=draws)
        return mean, lo, hi

    aggregates = []
    for h in ALL_ORDER:
        rs = by_h[h]
        execution_outcome, execution_lo, execution_hi = metric_summary(rs, "execution_outcome_score", 10000)
        outcome, outcome_lo, outcome_hi = metric_summary(rs, "outcome_score", 10000)
        process, process_lo, process_hi = metric_summary(rs, "process_score", 10000)
        process_unrounded = float(np.mean([row["process_effective_unrounded"] for row in rs])) if process is not None else None
        security, _, _ = metric_summary(rs, "security_score", 10000)
        combined, combined_lo, combined_hi = metric_summary(rs, "combined_score", 10000)
        aggregates.append({
            "harness_id": h, "harness": META[h][0], "model": META[h][1], "tasks": len(rs),
            "execution_outcome_score": execution_outcome,
            "execution_outcome_ci_low": execution_lo, "execution_outcome_ci_high": execution_hi,
            "outcome_score": outcome, "outcome_ci_low": outcome_lo, "outcome_ci_high": outcome_hi,
            "process_score": process, "process_ci_low": process_lo, "process_ci_high": process_hi,
            "process_effective_unrounded": process_unrounded,
            "security_score": security,
            "combined_score": combined, "combined_ci_low": combined_lo, "combined_ci_high": combined_hi,
            "process_grade_cost_usd": sum(row["process_grade_cost_usd"] or 0 for row in rs) if combined is not None else None,
            "elapsed_seconds": sum(r["elapsed_seconds"] for r in rs),
            "calls": sum(r["calls"] for r in rs),
            "input_tokens": sum(r["input_tokens"] for r in rs),
            "reported_input_tokens": sum(r["reported_input_tokens"] for r in rs),
            "cache_read_tokens": sum(r["cache_read_tokens"] for r in rs),
            "cache_write_tokens": sum(r["cache_write_tokens"] for r in rs),
            "output_tokens": sum(r["output_tokens"] for r in rs),
            "reasoning_tokens": sum(r["reasoning_tokens"] for r in rs),
            "total_tokens": sum(r["total_tokens"] for r in rs),
        })
    topic_rows = []
    for (h, topic), rs in sorted(by_ht.items()):
        execution_outcome, _, _ = metric_summary(rs, "execution_outcome_score", 5000)
        outcome, outcome_lo, outcome_hi = metric_summary(rs, "outcome_score", 5000)
        process, _, _ = metric_summary(rs, "process_score", 5000)
        process_unrounded = float(np.mean([row["process_effective_unrounded"] for row in rs])) if process is not None else None
        security, _, _ = metric_summary(rs, "security_score", 5000)
        combined, combined_lo, combined_hi = metric_summary(rs, "combined_score", 5000)
        topic_rows.append({"harness_id": h, "harness": META[h][0], "topic": topic, "tasks": len(rs),
                           "execution_outcome_score": execution_outcome,
                           "outcome_score": outcome, "outcome_ci_low": outcome_lo, "outcome_ci_high": outcome_hi,
                           "process_score": process, "process_effective_unrounded": process_unrounded,
                           "security_score": security,
                           "combined_score": combined, "combined_ci_low": combined_lo, "combined_ci_high": combined_hi})
    return aggregates, topic_rows


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def setup_style():
    plt.rcParams.update({
        "font.family": ["Avenir Next", "DejaVu Sans"], "font.size": 11,
        "axes.facecolor": PAPER, "figure.facecolor": PAPER, "savefig.facecolor": PAPER,
        "axes.edgecolor": INK, "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": INK, "axes.titleweight": "bold",
        "axes.spines.top": False, "axes.spines.right": False,
        "svg.fonttype": "none", "svg.hashsalt": "harnessbench-gpt54-report", "figure.dpi": 180,
    })


def save(fig, base: Path):
    fig.savefig(base.with_suffix(".png"), dpi=220, bbox_inches="tight")
    svg_path = base.with_suffix(".svg")
    fig.savefig(svg_path, bbox_inches="tight", metadata={"Date": None})
    # Matplotlib emits harmless path-command padding; normalize it so tracked
    # assets pass whitespace checks and remain byte-deterministic.
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n")
    plt.close(fig)


def plot_overall(agg: list[dict], out: Path):
    ordered = [next(x for x in agg if x["harness_id"] == h) for h in MAIN_ORDER]
    fig, ax = plt.subplots(figsize=(10.8, 6.8))
    x = np.arange(len(ordered))
    vals = np.array([row["combined_score"] * 100 for row in ordered])
    xerr = np.array([[row["combined_score"]-row["combined_ci_low"] for row in ordered],
                     [row["combined_ci_high"]-row["combined_score"] for row in ordered]]) * 100
    ax.bar(x, vals, color=[COLORS[row["harness_id"]] for row in ordered], width=.66,
           edgecolor=INK, linewidth=.7, yerr=xerr,
           error_kw={"ecolor": INK, "capsize": 4, "lw": 1})
    ax.set_xticks(x, [row["harness"] for row in ordered])
    ax.set_ylim(50, 100); ax.set_ylabel("Mean combined score (%)")
    ax.yaxis.grid(True, color=GRID, lw=.7); ax.set_axisbelow(True)
    for xi, val, row in zip(x, vals, ordered):
        label_y = row["combined_ci_high"] * 100 + 1.0
        ax.text(xi, label_y, f"{val:.1f}", ha="center", va="bottom", weight="bold")
    ax.set_title("Harness-Bench combined quality", loc="left", fontsize=22, pad=18)
    ax.text(0, 1.015, "Outcome × unrounded process mean × security · 106 tasks · GPT-5.4 medium · higher is better", transform=ax.transAxes, color=MUTED)
    ax.text(.995, .965, "FOCUSED AXIS · STARTS AT 50", transform=ax.transAxes, ha="right",
            fontsize=8, color="#8A3F3A", family="monospace", weight="bold")
    ax.text(0, -0.17, "Whiskers: 95% task-bootstrap interval (task-sampling uncertainty, not run-to-run variance).",
            transform=ax.transAxes, fontsize=9, color=MUTED, va="top")
    save(fig, out / "overall_quality")


def plot_topics(topic_rows: list[dict], out: Path):
    lookup = {(row["harness_id"], row["topic"]): row for row in topic_rows}
    topics = sorted({row["topic"] for row in topic_rows},
                    key=lambda topic: (-sum(row["tasks"] for row in topic_rows if row["topic"] == topic), topic))
    fig, axes = plt.subplots(4, 2, figsize=(15, 15), sharey=True)
    short_labels = ["Prime", "Pi", "Hermes\ndefault", "Codex"]
    for ax, topic in zip(axes.flat, topics):
        rows = [lookup[(h, topic)] for h in MAIN_ORDER]
        x = np.arange(len(rows)); vals = np.array([row["combined_score"]*100 for row in rows])
        err = np.array([[row["combined_score"]-row["combined_ci_low"] for row in rows],
                        [row["combined_ci_high"]-row["combined_score"] for row in rows]]) * 100
        ax.bar(x, vals, color=[COLORS[row["harness_id"]] for row in rows], edgecolor=INK,
               linewidth=.4, width=.67, yerr=err, error_kw={"ecolor": INK, "capsize": 2, "lw": .7})
        ax.set_xticks(x, short_labels, fontsize=8.5)
        ax.set_ylim(20, 105); ax.yaxis.grid(True,color=GRID,lw=.6); ax.set_axisbelow(True)
        for xi, val, row in zip(x, vals, rows):
            ypos = max(row["combined_ci_high"] * 100 + 1.0, 21.2)
            ax.text(xi, ypos, f"{val:.0f}", ha="center", va="bottom", fontsize=8, weight="bold")
        n=rows[0]["tasks"]; ax.set_title(f"{topic}\n$n={n}$", loc="left", fontsize=12, pad=8)
    for ax in axes[:,0]: ax.set_ylabel("Mean combined score (%)")
    fig.suptitle("Combined quality by task topic", x=.055, ha="left", fontsize=22, fontweight="bold", y=.995)
    fig.text(.055,.952,"Faceted using each task’s declared class · whiskers are 95% task-bootstrap intervals",color=MUTED)
    fig.text(.945,.952,"FOCUSED AXIS · STARTS AT 20",ha="right",fontsize=8,color="#8A3F3A",family="monospace",weight="bold")
    fig.text(.055,.005,"Headline comparison includes only the contained benchmark-default Hermes profile.",fontsize=9,color=MUTED)
    fig.subplots_adjust(hspace=.58,wspace=.28,top=.90,bottom=.06)
    save(fig,out/"topic_quality_facets")


def pareto(points, xkey):
    return sorted([p for p in points if not any((q[xkey] <= p[xkey] and q["combined_score"] >= p["combined_score"] and
                                                  (q[xkey] < p[xkey] or q["combined_score"] > p["combined_score"])) for q in points)],
                  key=lambda p:p[xkey])


def plot_frontier(agg, out):
    agg = [p for p in agg if p["harness_id"] in MAIN_ORDER]
    fig, axes=plt.subplots(1,2,figsize=(14,6),sharey=True)
    specs=[("total_tokens",1e6,"Total model-context tokens (millions)"),("elapsed_seconds",3600,"Total task runtime (hours)")]
    for ax,(key,scale,xlabel) in zip(axes,specs):
        label_dy = {"hermes_aggressive": .18, "hermes_focused": -.18, "prime": .10, "pi": -.10}
        for p in agg:
            x=p[key]/scale;y=p["combined_score"]*100
            ax.scatter(x,y,s=135,color=COLORS[p["harness_id"]],edgecolor=INK,lw=.8,zorder=3)
            dx=.18 if key=="total_tokens" else .035
            ax.text(x+dx,y+label_dy.get(p["harness_id"], .02),p["harness"],fontsize=9,va="center")
        fr=pareto(agg,key)
        ax.plot([p[key]/scale for p in fr],[p["combined_score"]*100 for p in fr],color=INK,lw=1.2,ls="--",zorder=2)
        ax.grid(True,color=GRID,lw=.7);ax.set_axisbelow(True);ax.set_xlabel(xlabel)
        ax.text(.02,.03,"Dashed: Pareto frontier",transform=ax.transAxes,color=MUTED,fontsize=9)
    axes[0].set_ylabel("Mean combined score (%)")
    lo=min(p["combined_score"] for p in agg)*100-1;hi=max(p["combined_score"] for p in agg)*100+1
    axes[0].set_ylim(lo,hi)
    fig.suptitle("Quality–efficiency frontier",x=.06,ha="left",fontsize=22,fontweight="bold",y=.985)
    fig.text(.06,.885,"Higher and farther left is better · totals across the same 106 tasks",color=MUTED)
    fig.text(.06,.015,"Runtime is summed task elapsed time, not end-to-end wall-clock.",fontsize=9,color=MUTED)
    fig.subplots_adjust(top=.79,bottom=.14,wspace=.16)
    save(fig,out/"quality_efficiency_frontier")


def plot_task_facets(rows, out, metric, filename, xlabel):
    by_h=defaultdict(list)
    for r in rows: by_h[r["harness_id"]].append(r)
    fig,axes=plt.subplots(2,2,figsize=(13,10),sharey=True)
    for ax,h in zip(axes.flat,MAIN_ORDER):
        rs=by_h[h];x=np.array([r[metric] for r in rs],float);y=np.array([r["combined_score"]*100 for r in rs])
        lx=np.log10(np.maximum(x,1e-9))
        for topic,color in TOPIC_COLORS.items():
            ids=[i for i,r in enumerate(rs) if r["topic"]==topic]
            ax.scatter(x[ids],y[ids],s=25,alpha=.72,color=color,edgecolor="white",lw=.3)
        coef=np.polyfit(lx,y,1);grid=np.logspace(lx.min(),lx.max(),100)
        ax.plot(grid,np.polyval(coef,np.log10(grid)),color=INK,lw=1.5)
        corr=float(np.corrcoef(lx,y)[0,1]);ax.text(.97,.05,f"r = {corr:+.2f}",transform=ax.transAxes,ha="right",color=MUTED,fontsize=9)
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(LogLocator(base=10, numticks=4)); ax.xaxis.set_major_formatter(LogFormatterMathtext(base=10))
        ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * .1)); ax.xaxis.set_minor_formatter(NullFormatter())
        ax.grid(True,color=GRID,lw=.6);ax.set_axisbelow(True);ax.set_title(META[h][0],loc="left")
        ax.set_ylim(-4,104)
    for ax in axes[:,0]:ax.set_ylabel("Task combined score (%)")
    for ax in axes[-1]:ax.set_xlabel(xlabel)
    handles=[Line2D([0],[0],marker='o',linestyle='',markerfacecolor=c,markeredgecolor='none',label=t,markersize=7) for t,c in TOPIC_COLORS.items()]
    fig.legend(handles=handles,loc="lower center",ncol=4,frameon=False,fontsize=9,bbox_to_anchor=(.5,.045))
    fig.suptitle(f"Task-level combined score vs. {xlabel.lower()}",x=.06,ha="left",fontsize=22,fontweight="bold")
    fig.text(.06,.93,"Each point is one task · color is declared topic · line is descriptive OLS on log resource use",color=MUTED)
    fig.text(.06,.02,"Correlations are descriptive, not causal: task difficulty influences both score and resource use.",fontsize=9,color=MUTED)
    fig.subplots_adjust(top=.86,bottom=.21,hspace=.3,wspace=.17)
    save(fig,out/filename)


def comparison_markdown(agg):
    lines=["# Harness-Bench comparison table", "",
           "Combined score is computed per task as outcome × the unrounded mean of the three process-rubric dimensions × security, then rounded to four decimals using the evaluator’s Python float arithmetic. The separately stored process_score/process_effective field rounds that same mean to four decimals, so multiplying the displayed rounded components can differ by 0.0001. All four headline options use the same pinned Claude Sonnet 4.6 judge and complete, untruncated traces. Input is standardized as Total − Cache read − Output.", "",
           "| Option | Combined | Outcome | Process | Security | Input tokens | Cache read tokens | Output tokens | Total tokens | Calls | Runtime |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for x in sorted((r for r in agg if r["harness_id"] in MAIN_ORDER), key=lambda r:-r["combined_score"]):
        lines.append(f"| {x['harness']} | {x['combined_score']*100:.2f}% | {x['outcome_score']*100:.2f}% | {x['process_effective_unrounded']*100:.2f}% | {x['security_score']*100:.0f}% | {x['input_tokens']:,} | {x['cache_read_tokens']:,} | {x['output_tokens']:,} | {x['total_tokens']:,} | {x['calls']:,} | {x['elapsed_seconds']/3600:.2f} h |")
    lines += ["", "Combined is averaged after the taskwise multiplication; it is not the product of the displayed aggregate means. The Process column is aggregated from the exported unrounded rubric means; the normalized CSV also preserves the official four-decimal process_score. The outcome component includes the benchmark’s configured quality-LLM blend on tasks 008 and 013.",
              "", "Excluded: original Hermes (oracle leakage); Claude Code pilot (systemic tool failures; not a valid baseline).",
              "", "## Hermes profile comparison", "",
              "Contained default is the headline Hermes result. Focused/aggressive were a separate, counterbalanced post-hoc ablation and have not been process-graded, so this table retains their execution-time raw outcome scores.", "",
              "| Hermes profile | Role | Raw outcome | Total tokens | Calls | Runtime |", "|---|---|---:|---:|---:|---:|"]
    for h,role in [("hermes_default","Headline contained rerun"),("hermes_focused","Post-hoc: fixed skills surface"),("hermes_aggressive","Post-hoc: same surface minus skills")]:
        x=next(r for r in agg if r["harness_id"]==h)
        raw = x["execution_outcome_score"]
        lines.append(f"| {x['harness'].replace('*','')} | {role} | {raw*100:.2f}% | {x['total_tokens']:,} | {x['calls']:,} | {x['elapsed_seconds']/3600:.2f} h |")
    lines += ["", "Aggressive minus focused: **+0.21 percentage points**; paired wins/ties/losses **15/67/24**; paired t-test **p=0.861**; task-bootstrap 95% CI approximately **[-2.12, +2.59] points**. No statistically detectable quality difference was observed in this single-run paired comparison, while aggressive used about 40% fewer tokens, 9.9% fewer calls, and 4.9% less runtime."]
    return "\n".join(lines)+"\n"


def render_html(agg):
    table=[]
    for x in sorted((r for r in agg if r["harness_id"] in MAIN_ORDER),key=lambda r:-r["combined_score"]):
        table.append("<tr>"+"".join(f"<td>{v}</td>" for v in [html.escape(x["harness"]),f"{x['combined_score']*100:.2f}%",f"{x['outcome_score']*100:.2f}%",f"{x['process_effective_unrounded']*100:.2f}%",f"{x['security_score']*100:.0f}%",f"{x['input_tokens']:,}",f"{x['cache_read_tokens']:,}",f"{x['output_tokens']:,}",f"{x['total_tokens']:,}",f"{x['calls']:,}",f"{x['elapsed_seconds']/3600:.2f} h"])+"</tr>")
    hermes_table=[]
    for h,role in [("hermes_default","Headline contained rerun"),("hermes_focused","Post-hoc fixed skills"),("hermes_aggressive","Post-hoc minus skills")]:
        x=next(r for r in agg if r["harness_id"]==h)
        hermes_table.append("<tr>"+"".join(f"<td>{v}</td>" for v in [html.escape(x["harness"].replace("*","")),role,f"{x['execution_outcome_score']*100:.2f}%",f"{x['total_tokens']:,}",f"{x['calls']:,}",f"{x['elapsed_seconds']/3600:.2f} h"])+"</tr>")
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Harness-Bench comparison</title><style>
    :root{{--ink:#111;--paper:#fafaf8;--muted:#6f716d;--accent:#85ed75;--line:#d9dad5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Avenir Next",Inter,system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:54px 28px 90px}}.eyebrow{{font:12px ui-monospace,monospace;letter-spacing:.12em}}h1{{font-size:54px;line-height:.98;max-width:850px;margin:18px 0}}.lede{{font-size:20px;color:var(--muted);max-width:840px}}.rule{{height:8px;background:var(--accent);width:128px;margin:28px 0 60px}}section{{margin:70px 0}}h2{{font-size:30px;margin-bottom:8px}}p.note{{color:var(--muted);max-width:900px}}img{{width:100%;display:block;border:1px solid var(--line);background:var(--paper)}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{padding:13px 10px;border-bottom:1px solid var(--line);text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{font-size:12px;text-transform:uppercase;letter-spacing:.06em}}.callout{{border-left:8px solid var(--accent);padding:5px 20px}}code{{font-family:ui-monospace,monospace}}@media(max-width:700px){{h1{{font-size:38px}}.wide{{overflow-x:auto}}}}
    </style></head><body><main><div class="eyebrow">HARNESS-BENCH / INTERIM AUDITED COMPARISON</div><h1>Quality and efficiency across agent harnesses</h1><p class="lede">A like-for-like comparison on 106 real-workspace tasks using GPT-5.4 medium. This interim edition includes only complete, audited datasets.</p><div class="rule"></div>
    <section><h2>Overall combined quality</h2><p class="note">Combined score is computed per task as outcome × the unrounded mean of tool-use appropriateness, consistency, and robustness × security, then rounded to four decimals using the evaluator’s Python float arithmetic. All four headline options use the same pinned Claude Sonnet 4.6 judge and complete, untruncated full-trace-v2 evidence.</p><img src="figures/overall_quality.svg" alt="Overall combined quality bar chart"></section>
    <section><h2>Score components, tokens, and runtime</h2><div class="wide"><table><thead><tr><th>Option</th><th>Combined</th><th>Outcome</th><th>Process</th><th>Security</th><th>Input tokens</th><th>Cache read tokens</th><th>Output tokens</th><th>Total tokens</th><th>Calls</th><th>Runtime</th></tr></thead><tbody>{''.join(table)}</tbody></table></div><p class="note">Combined is averaged after the taskwise multiplication, so it is not the product of the displayed aggregate means. Process displays the exported unrounded rubric mean; the normalized CSV also retains the official four-decimal <code>process_score</code>. Multiplying only the rounded stored fields can differ from retained combined by 0.0001. Outcome includes the benchmark’s configured quality-LLM blend on tasks 008 and 013. Input is standardized as Total − Cache read − Output; the three token components are non-overlapping and sum to Total. Reasoning tokens are already contained in output. Runtime is summed task elapsed time.</p></section>
    <section class="callout"><h2>Audited composition</h2><p><strong>Codex:</strong> 105 entries from the initial full manifest plus the second, valid operational correction for task 023. The initial task and failed first correction remain preserved and excluded.</p><p><strong>Process grading:</strong> each headline task is bound to its source result, retained proxy trace, workspace, and exact rubric request by SHA-256. Legacy 24,000-character-prefix grades and the contaminated original Hermes grades are not used.</p></section>
    <section><h2>Combined quality by topic</h2><img src="figures/topic_quality_facets.svg" alt="Quality by task topic"></section>
    <section><h2>Combined quality–efficiency frontier</h2><img src="figures/quality_efficiency_frontier.svg" alt="Quality efficiency frontier"></section>
    <section><h2>Task-level detail</h2><p class="note">The regression lines are descriptive only; task difficulty affects both score and resource use.</p><img src="figures/task_tokens_facets.svg" alt="Task score versus tokens"><br><img src="figures/task_runtime_facets.svg" alt="Task score versus runtime"></section>
    <section><h2>Hermes profile note</h2><p class="note">Only contained benchmark-default Hermes appears in the headline charts. The other profiles came from a separate counterbalanced post-hoc ablation and were not process-graded; their table values remain execution-time raw outcomes.</p><div class="wide"><table><thead><tr><th>Hermes profile</th><th>Role</th><th>Raw outcome</th><th>Total tokens</th><th>Calls</th><th>Runtime</th></tr></thead><tbody>{''.join(hermes_table)}</tbody></table></div><p>No statistically detectable quality difference was observed between focused and aggressive in this single-run paired comparison: aggressive minus focused was <strong>+0.21 points</strong> (wins/ties/losses 15/67/24; paired t-test p=0.861; task-bootstrap 95% CI ≈ [-2.12, +2.59] points). Aggressive used about <strong>40% fewer tokens</strong>, 9.9% fewer calls, and 4.9% less runtime.</p><p class="note">Six aggressive cells (091, 094, 096, 097, 099, 106) use separately preserved corrections after a terminal audit classified their originals as watchdog-truncated infrastructure failures.</p></section>
    <section class="callout"><h2>Scope and exclusions</h2><p><strong>Original Hermes is excluded</strong> after confirmed access to oracle/ground-truth material. The contained benchmark-default rerun is the Hermes headline result.</p><p><strong>The Claude Code pilot is not a baseline.</strong> Nine completed tasks averaged 9.3%, but all 51 Bash calls failed before shell startup with <code>E2BIG</code>, and all 30 Write plus 8 Edit calls were policy-denied. With no mutation channel, the tasks were technically impossible to complete. The 2.08M standardized model-context tokens were retry-contaminated and only 11% above default Hermes on the same nine tasks, so they do not establish normal Claude inefficiency. Any future attempt must first pass an exact production Write/Edit/Bash containment canary and a single-task smoke. The outcome component for tasks 008 and 013 uses the benchmark’s configured quality-LLM rubric and therefore includes judge uncertainty.</p></section>
    <section><p class="eyebrow">GENERATED FROM AUDITED RESULTS AND FULL-TRACE-V2 PROCESS GRADES · SNAPSHOT DATA INCLUDED</p></section></main></body></html>'''


def main():
    here = Path(__file__).resolve()
    default_repo = next(p for p in here.parents if (p / "pyproject.toml").exists())
    parser=argparse.ArgumentParser();parser.add_argument("--repo",type=Path,default=default_repo);parser.add_argument("--runs",type=Path,default=Path.home()/".harnessbench/runs")
    args=parser.parse_args();out=Path(__file__).resolve().parent;data=out/"data";figures=out/"figures";data.mkdir(exist_ok=True);figures.mkdir(exist_ok=True)
    rows,_=load_inputs(args.repo,args.runs);agg,topics=aggregate(rows)
    # Lock expected audited aggregates so a wrong source cannot silently generate polished charts.
    expected={
        "prime": (0.8665018868,5365811,6742.584,670,1387583,3663872,314356),
        "pi": (0.8612254717,2650418,7031.182,558,853187,1489152,308079),
        "codex": (0.8026264151,15810187,10517.753,649,15304660,13760384,505527),
        "hermes_default": (0.8451179245,20975574,12228.404,966,2292231,18115968,567375),
        "hermes_focused": (0.8509113208,16762811,12368.497,1022,2502152,13687296,573363),
        "hermes_aggressive": (0.8530311321,10046032,11767.865,921,1808935,7692672,544425),
    }
    for x in agg:
        score,tokens,elapsed,calls,input_tokens,cache_read,output_tokens=expected[x["harness_id"]]
        if not (abs(x["execution_outcome_score"]-score)<5e-10 and x["total_tokens"]==tokens and
                abs(x["elapsed_seconds"]-elapsed)<.02 and x["calls"]==calls and
                x["reported_input_tokens"]==input_tokens and x["cache_read_tokens"]==cache_read and
                x["output_tokens"]==output_tokens and x["cache_write_tokens"]==0 and
                x["input_tokens"] + x["cache_read_tokens"] + x["output_tokens"] == x["total_tokens"]):
            raise RuntimeError(f"published aggregate drift for {x['harness_id']}: {x}")
    expected_scoring = {
        "prime": (0.8607283018867925, 0.9627056603773584, 0.9627044025157233, 1.0, 0.8296471698113207, 4.45293585),
        "pi": (0.8557066037735849, 0.9632716981132076, 0.9632704402515724, 1.0, 0.8248132075471698, 3.89638458),
        "hermes_default": (0.8407877358490566, 0.9282405660377356, 0.9282389937106919, 1.0, 0.7809622641509435, 9.04149378),
        "codex": (0.8004188679245283, 0.901732075471698, 0.9017295597484276, 1.0, 0.7339575471698113, 4.18290642),
    }
    for harness, values in expected_scoring.items():
        row = next(item for item in agg if item["harness_id"] == harness)
        actual = tuple(row[key] for key in ("outcome_score", "process_score", "process_effective_unrounded", "security_score", "combined_score", "process_grade_cost_usd"))
        if any(abs(float(got) - expected) > 5e-12 for got, expected in zip(actual, values)):
            raise RuntimeError(f"published full-trace-v2 scoring drift for {harness}: {actual}")
    manifest_specs = [
        (args.repo / "reports/process-grade-claude-sonnet-4.6-full-trace-v2-manifest.json", 8.34932043),
        (args.repo / "evaluation/process-grades/claude-sonnet-4.6-full-trace-v2-corrected-manifest.json", 13.22440020),
    ]
    for manifest_path, expected_cost in manifest_specs:
        manifest = read_json(manifest_path)
        if (manifest.get("status") != "complete" or manifest.get("trace_policy") != "full_valid_json" or
                manifest.get("max_payload_chars") != 1_000_000 or len(manifest.get("tasks", [])) != 106 or
                len(manifest.get("results", [])) != 212 or abs(float(manifest.get("reported_cost_usd", -1)) - expected_cost) > 5e-8):
            raise RuntimeError(f"process-grade manifest drift: {manifest_path}")
    write_csv(data/"task_metadata.csv", task_metadata_rows(args.repo))
    write_csv(data/"authoritative_task_metrics.csv",rows);write_csv(data/"aggregate_metrics.csv",agg);write_csv(data/"topic_metrics.csv",topics)
    (data / "provenance.json").write_text(json.dumps(build_provenance(args.repo, args.runs), indent=2, sort_keys=True) + "\n")
    write_csv(data/"exclusions.csv",[
        {"option":"Original Hermes GPT-5.4 medium","status":"excluded","reason":"confirmed oracle/ground-truth leakage on tasks 037, 046, 055, and 079"},
        {"option":"Claude Code Opus 4.6 medium","status":"invalid_pilot","reason":"nine completed tasks had no mutation channel: 51/51 Bash calls failed E2BIG, 30/30 Write and 8/8 Edit were policy-denied; not interpretable as model quality"},
    ])
    setup_style();plot_overall(agg,figures);plot_topics(topics,figures);plot_frontier(agg,figures)
    plot_task_facets(rows,figures,"total_tokens","task_tokens_facets","Model-context tokens per task")
    plot_task_facets(rows,figures,"elapsed_seconds","task_runtime_facets","Runtime seconds per task")
    (out/"comparison_table.md").write_text(comparison_markdown(agg));(out/"index.html").write_text(render_html(agg))
    print(f"wrote {len(rows)} authoritative task rows and {len(agg)} options to {out}")

if __name__=="__main__": main()