#!/usr/bin/env python3
"""Plot Prime Agent against the authors' published GPT-5.4 task scores."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ["MPLBACKEND"] = "Agg"
import matplotlib.pyplot as plt
import numpy as np
import yaml


HARNESS_LABELS = {
    "codex": "Codex",
    "nanobot": "NanoBot",
    "moltis-local": "Moltis",
    "openclaw-local": "OpenClaw",
    "hermes": "Hermes",
    "nullclaw": "NullClaw",
    "zeroclaw-local": "ZeroClaw",
}
CATEGORY_LABELS = {
    "Software Engineering & Codebase Maintenance": "Software\nengineering",
    "Data, BI & Finance Analytics": "Data / BI",
    "Long-running Autonomy & State Adaptation": "Long-running\nautonomy",
}
COLORS = {
    "Prime Agent": "#2563eb",
    "Codex": "#e76f51",
    "NanoBot": "#2a9d8f",
    "other": "#a8b0bb",
}


def task_classes(repo: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for task_file in sorted((repo / "tasks").glob("*/task.yaml")):
        data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        result[task_file.parent.name] = str(data.get("class", ""))
    return result


def prime_rows(repo: Path, task_ids: list[str], harness: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task_id in task_ids:
        matches = list((repo / "data_try6" / "results" / harness).glob(f"*/{task_id}.json"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one Prime result for {task_id}, found {matches}")
        data = json.loads(matches[0].read_text(encoding="utf-8"))
        oracle = data.get("oracle_result") or {}
        score = oracle.get("outcome_score", oracle.get("score"))
        rows.append({"task_id": task_id, "completion": float(score)})
    return rows


def mean_for(rows: list[dict[str, Any]], task_ids: set[str]) -> float:
    values = [float(row["completion"]) for row in rows if row["task_id"] in task_ids]
    if len(values) != len(task_ids):
        raise RuntimeError(f"expected {len(task_ids)} scores, found {len(values)}")
    return float(np.mean(values))


def save(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    for suffix in ("png", "svg"):
        fig.savefig(output_dir / f"{stem}.{suffix}", dpi=220, bbox_inches="tight", facecolor="white")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", default="prime-agent-gpt-5.4-medium")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("evaluation/results/published-gpt-5.4-subset-baseline.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/results"))
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    baseline = json.loads((repo / args.baseline).read_text(encoding="utf-8"))
    task_ids = list(baseline["task_ids"])
    classes = task_classes(repo)
    published = list(baseline["runs"])
    prime = prime_rows(repo, task_ids, args.harness)
    for row in prime:
        row["harness"] = "prime-agent"
    by_harness = {h: [row for row in published if row["harness"] == h] for h in HARNESS_LABELS}
    if any(len(rows) != len(task_ids) for rows in by_harness.values()):
        raise RuntimeError("published baseline does not contain every selected task/harness pair")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False})

    # Figure 1: exact-subset overall and category completion.
    overall = [("Prime Agent", mean_for(prime, set(task_ids)))] + [
        (HARNESS_LABELS[h], mean_for(rows, set(task_ids))) for h, rows in by_harness.items()
    ]
    overall.sort(key=lambda item: item[1], reverse=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.8), gridspec_kw={"width_ratios": [1.0, 1.28]})
    labels = [item[0] for item in overall][::-1]
    values = [item[1] for item in overall][::-1]
    bar_colors = [COLORS.get(label, COLORS["other"]) for label in labels]
    ax1.barh(labels, np.array(values) * 100, color=bar_colors)
    ax1.set_xlim(0, 100)
    ax1.set_xlabel("Mean completion / oracle outcome (%)")
    ax1.set_title("A. GPT-5.4, exact 47-task subset")
    ax1.grid(axis="x", alpha=0.18)
    for index, value in enumerate(values):
        ax1.text(value * 100 + 0.8, index, f"{value * 100:.1f}", va="center", fontsize=9)

    category_order = list(CATEGORY_LABELS)
    series = [
        ("Prime Agent", prime),
        ("Codex", by_harness["codex"]),
        ("NanoBot", by_harness["nanobot"]),
    ]
    x = np.arange(len(category_order))
    width = 0.24
    for offset, (label, rows) in zip((-width, 0.0, width), series):
        vals = [mean_for(rows, {tid for tid in task_ids if classes[tid] == category}) * 100 for category in category_order]
        bars = ax2.bar(x + offset, vals, width=width, label=label, color=COLORS[label])
        ax2.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
    ax2.set_ylim(0, 100)
    ax2.set_xticks(x, [CATEGORY_LABELS[category] for category in category_order])
    ax2.set_ylabel("Mean completion / oracle outcome (%)")
    ax2.set_title("B. Category breakdown for the top three")
    ax2.grid(axis="y", alpha=0.18)
    ax2.legend(frameon=False, loc="lower left")
    fig.suptitle("Prime Agent is effectively tied with Codex on the decision-relevant subset", fontsize=15, fontweight="bold")
    fig.text(
        0.5,
        -0.015,
        "All bars use GPT-5.4 and the same 47 task IDs. Prime used subscription openai-codex, thinking=medium; "
        "published result metadata omits effort and harness versions. Outcome only—Prime process grading was skipped.\n"
        "Published source: harness-bench.ai/assets/leaderboard_scores.json (generated 2026-05-20).",
        ha="center",
        va="top",
        fontsize=8.5,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.07, 1, 0.94))
    save(fig, args.output_dir, "prime-vs-published-gpt54-subset")
    plt.close(fig)

    # Figure 2: paired task comparison against the strongest directly comparable reference.
    prime_map = {row["task_id"]: float(row["completion"]) for row in prime}
    codex_map = {row["task_id"]: float(row["completion"]) for row in by_harness["codex"]}
    diffs = {task_id: prime_map[task_id] - codex_map[task_id] for task_id in task_ids}
    wins = sum(delta > 1e-9 for delta in diffs.values())
    ties = sum(abs(delta) <= 1e-9 for delta in diffs.values())
    losses = sum(delta < -1e-9 for delta in diffs.values())
    category_colors = {
        "Software Engineering & Codebase Maintenance": "#7c3aed",
        "Data, BI & Finance Analytics": "#059669",
        "Long-running Autonomy & State Adaptation": "#d97706",
    }
    fig, ax = plt.subplots(figsize=(8.2, 7.3))
    for category in category_order:
        ids = [tid for tid in task_ids if classes[tid] == category]
        ax.scatter(
            [codex_map[tid] for tid in ids],
            [prime_map[tid] for tid in ids],
            s=58,
            alpha=0.82,
            color=category_colors[category],
            edgecolor="white",
            linewidth=0.7,
            label=CATEGORY_LABELS[category].replace("\n", " "),
        )
    ax.plot([0.2, 1.02], [0.2, 1.02], linestyle="--", color="#6b7280", linewidth=1.2, label="Equal outcome")
    for task_id, delta in diffs.items():
        if abs(delta) >= 0.045:
            prefix = task_id.split("-", 1)[0]
            offsets = {"014": (-20, -12), "104": (5, -12), "105": (5, 4), "106": (-18, 4)}
            ax.annotate(prefix, (codex_map[task_id], prime_map[task_id]), xytext=offsets.get(prefix, (5, 4)), textcoords="offset points", fontsize=8)
    ax.set_xlim(0.2, 1.025)
    ax.set_ylim(0.2, 1.025)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Published Codex completion")
    ax.set_ylabel("Prime Agent oracle outcome")
    ax.set_title(f"Paired task outcomes: Prime wins {wins}, ties {ties}, loses {losses}\nMean: Prime {np.mean(list(prime_map.values())):.3f} vs Codex {np.mean(list(codex_map.values())):.3f}")
    ax.grid(alpha=0.16)
    ax.legend(frameon=False, loc="lower right", fontsize=8.5)
    fig.text(
        0.5,
        0.01,
        "Labels show task-number prefixes where |Prime − Codex| ≥ 0.045. Scores are one published run and one Prime run per task; no uncertainty bars.\n"
        "Same task IDs and model family, but backend revision, hidden prompts, versions, and published reasoning effort are not fully preserved.",
        ha="center",
        va="bottom",
        fontsize=8.3,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.075, 1, 1))
    save(fig, args.output_dir, "prime-vs-codex-gpt54-taskwise")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
