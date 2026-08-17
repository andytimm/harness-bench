#!/usr/bin/env python3
"""Build the audited, shareable Harness-Bench comparison.

The report deliberately excludes the contaminated original Hermes run and any
incomplete Claude results. It uses raw outcome score because post-hoc process
scores are not yet available for every included harness.
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
        "codex": [repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/full/manifest.json", repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/manifest.json", repo / "evaluation/runs/codex-gpt-5.4-medium-current-0.139/correction-023-loopback-network/CORRECTION_ATTEMPT_AUDIT.json", Path.home() / ".harnessbench/evaluation-gates/codex-gpt-5.4-medium-current-0.139/AUDIT.json"],
        "hermes_default": list((runs / "post-codex-contained-default-hermes-106/results/rerun/post-codex-hermes-contained-default-106/gpt-5.4").glob("*.json")) + [runs / "post-codex-contained-default-hermes-106/POST_RUN_AUDIT.json", runs / "post-codex-contained-default-hermes-106/INDEPENDENT_REVIEW.json"],
        "hermes_fixed_common": [runs / "post-codex-hermes-fixed-106/POST_RUN_AUDIT.json"],
        "hermes_focused": list((runs / "post-codex-hermes-fixed-106/profiles/focused-with-skills/results/ablation/post-codex-hermes-fixed-profiles-106/focused-with-skills/gpt-5.4").glob("*.json")),
        "hermes_aggressive": list((runs / "post-codex-hermes-fixed-106/profiles/aggressive-no-skills/results/ablation/post-codex-hermes-fixed-profiles-106/aggressive-no-skills/gpt-5.4").glob("*.json")) + list((runs / "post-codex-hermes-watchdog-corrections-6/results/correction/post-codex-hermes-fixed-aggressive-watchdog-v1/gpt-5.4").glob("*.json")) + [runs / "post-codex-hermes-watchdog-corrections-6/CORRECTION_AUDIT.json"],
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
    return {"schema": 1, "metric": "scoring.outcome_score", "task_count_per_option": 106,
            "input_count": len(entries), "inputs": entries,
            "composition": {"codex": "105 full-manifest entries plus correction-023-loopback-network task 023", "hermes_aggressive": "100 fixed-root results plus six watchdog-correction results (091,094,096,097,099,106)"},
            "exclusions": {"original_hermes": {"tasks": list(leak_tasks), "finding": "retained result records contain ground_truth and benchmark task-root access markers"},
                           "claude_pilot": {"tasks": list(claude_first + claude_second), "mean_score": 0.09333333333333332, "finding": "all 51 Bash calls failed before shell startup with E2BIG; all 30 Write and 8 Edit calls were policy-denied; no mutation channel, so not a model-quality baseline"}}}


def result_row(task_id: str, result: dict, harness: str, topic: str, provenance: str) -> dict:
    usage = result["usage_summary"]
    scoring = result["scoring"]
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
    # Use the score recorded by the common scoring pipeline for every harness.
    score = scoring["outcome_score"]
    return {
        "harness_id": harness,
        "harness": META[harness][0],
        "model": META[harness][1],
        "task_id": task_id,
        "topic": topic,
        "score": float(score),
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

    rows = []
    for harness, (results, provenance) in datasets.items():
        if set(results) != set(topics):
            raise RuntimeError(f"task mismatch for {harness}")
        for task_id in sorted(results):
            rows.append(result_row(task_id, results[task_id], harness, topics[task_id], provenance))
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
    aggregates = []
    for h in ALL_ORDER:
        rs = by_h[h]
        scores = [r["score"] for r in rs]
        lo, hi = bootstrap_mean_ci(scores, rng)
        aggregates.append({
            "harness_id": h, "harness": META[h][0], "model": META[h][1], "tasks": len(rs),
            "score": float(np.mean(scores)), "score_ci_low": lo, "score_ci_high": hi,
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
        scores = [r["score"] for r in rs]
        lo, hi = bootstrap_mean_ci(scores, rng, draws=5000)
        topic_rows.append({"harness_id": h, "harness": META[h][0], "topic": topic, "tasks": len(rs),
                           "score": float(np.mean(scores)), "score_ci_low": lo, "score_ci_high": hi})
    return aggregates, topic_rows


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
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
    fig.savefig(base.with_suffix(".svg"), bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def plot_overall(agg: list[dict], out: Path):
    ordered = [next(x for x in agg if x["harness_id"] == h) for h in MAIN_ORDER]
    fig, ax = plt.subplots(figsize=(10.8, 6.8))
    x = np.arange(len(ordered))
    vals = np.array([row["score"] * 100 for row in ordered])
    xerr = np.array([[row["score"]-row["score_ci_low"] for row in ordered],
                     [row["score_ci_high"]-row["score"] for row in ordered]]) * 100
    ax.bar(x, vals, color=[COLORS[row["harness_id"]] for row in ordered], width=.66,
           edgecolor=INK, linewidth=.7, yerr=xerr,
           error_kw={"ecolor": INK, "capsize": 4, "lw": 1})
    ax.set_xticks(x, [row["harness"] for row in ordered])
    ax.set_ylim(50, 100); ax.set_ylabel("Mean raw outcome score (%)")
    ax.yaxis.grid(True, color=GRID, lw=.7); ax.set_axisbelow(True)
    for xi, val, row in zip(x, vals, ordered):
        label_y = row["score_ci_high"] * 100 + 1.0
        ax.text(xi, label_y, f"{val:.1f}", ha="center", va="bottom", weight="bold")
    ax.set_title("Harness-Bench quality", loc="left", fontsize=22, pad=18)
    ax.text(0, 1.015, "106 real-workspace tasks · GPT-5.4 medium · higher is better", transform=ax.transAxes, color=MUTED)
    ax.text(.995, .965, "FOCUSED AXIS · STARTS AT 50", transform=ax.transAxes, ha="right",
            fontsize=8, color="#8A3F3A", family="monospace", weight="bold")
    ax.text(0, -0.17, "Whiskers: 95% task-bootstrap interval (task-sampling uncertainty, not run-to-run variance).",
            transform=ax.transAxes, fontsize=9, color=MUTED, va="top")
    ax.text(0, -.235, "NOT RANKED  ·  Original Hermes: oracle leakage  ·  Claude pilot: stopped after systemic tool failures",
            transform=ax.transAxes, fontsize=9, color="#8A3F3A", va="top", weight="bold")
    ax.text(.995, -.285, "PRIME-INSPIRED / INDEPENDENT ANALYSIS", transform=ax.transAxes, ha="right", va="top",
            fontsize=8, color=MUTED, family="monospace")
    save(fig, out / "overall_quality")


def plot_topics(topic_rows: list[dict], out: Path):
    lookup = {(row["harness_id"], row["topic"]): row for row in topic_rows}
    topics = sorted({row["topic"] for row in topic_rows},
                    key=lambda topic: (-sum(row["tasks"] for row in topic_rows if row["topic"] == topic), topic))
    fig, axes = plt.subplots(4, 2, figsize=(15, 15), sharey=True)
    short_labels = ["Prime", "Pi", "Hermes\ndefault", "Codex"]
    for ax, topic in zip(axes.flat, topics):
        rows = [lookup[(h, topic)] for h in MAIN_ORDER]
        x = np.arange(len(rows)); vals = np.array([row["score"]*100 for row in rows])
        err = np.array([[row["score"]-row["score_ci_low"] for row in rows],
                        [row["score_ci_high"]-row["score"] for row in rows]]) * 100
        ax.bar(x, vals, color=[COLORS[row["harness_id"]] for row in rows], edgecolor=INK,
               linewidth=.4, width=.67, yerr=err, error_kw={"ecolor": INK, "capsize": 2, "lw": .7})
        ax.set_xticks(x, short_labels, fontsize=8.5)
        ax.set_ylim(45, 105); ax.yaxis.grid(True,color=GRID,lw=.6); ax.set_axisbelow(True)
        for xi, val, row in zip(x, vals, rows):
            ypos = max(row["score_ci_high"] * 100 + 1.0, 46.2)
            ax.text(xi, ypos, f"{val:.0f}", ha="center", va="bottom", fontsize=8, weight="bold")
        n=rows[0]["tasks"]; ax.set_title(f"{topic}\n$n={n}$", loc="left", fontsize=12, pad=8)
    for ax in axes[:,0]: ax.set_ylabel("Mean outcome score (%)")
    fig.suptitle("Quality by task topic", x=.055, ha="left", fontsize=22, fontweight="bold", y=.995)
    fig.text(.055,.952,"Faceted using each task’s declared class · whiskers are 95% task-bootstrap intervals",color=MUTED)
    fig.text(.945,.952,"FOCUSED AXIS · STARTS AT 45",ha="right",fontsize=8,color="#8A3F3A",family="monospace",weight="bold")
    fig.text(.055,.005,"Headline comparison includes only the contained benchmark-default Hermes profile.",fontsize=9,color=MUTED)
    fig.subplots_adjust(hspace=.58,wspace=.28,top=.90,bottom=.06)
    save(fig,out/"topic_quality_facets")


def pareto(points, xkey):
    return sorted([p for p in points if not any((q[xkey] <= p[xkey] and q["score"] >= p["score"] and
                                                  (q[xkey] < p[xkey] or q["score"] > p["score"])) for q in points)],
                  key=lambda p:p[xkey])


def plot_frontier(agg, out):
    agg = [p for p in agg if p["harness_id"] in MAIN_ORDER]
    fig, axes=plt.subplots(1,2,figsize=(14,6),sharey=True)
    specs=[("total_tokens",1e6,"Total model-context tokens (millions)"),("elapsed_seconds",3600,"Total task runtime (hours)")]
    for ax,(key,scale,xlabel) in zip(axes,specs):
        label_dy = {"hermes_aggressive": .18, "hermes_focused": -.18, "prime": .10, "pi": -.10}
        for p in agg:
            x=p[key]/scale;y=p["score"]*100
            ax.scatter(x,y,s=135,color=COLORS[p["harness_id"]],edgecolor=INK,lw=.8,zorder=3)
            dx=.18 if key=="total_tokens" else .035
            ax.text(x+dx,y+label_dy.get(p["harness_id"], .02),p["harness"],fontsize=9,va="center")
        fr=pareto(agg,key)
        ax.plot([p[key]/scale for p in fr],[p["score"]*100 for p in fr],color=INK,lw=1.2,ls="--",zorder=2)
        ax.grid(True,color=GRID,lw=.7);ax.set_axisbelow(True);ax.set_xlabel(xlabel)
        ax.text(.02,.03,"Dashed: Pareto frontier",transform=ax.transAxes,color=MUTED,fontsize=9)
    axes[0].set_ylabel("Mean raw outcome score (%)")
    lo=min(p["score"] for p in agg)*100-1;hi=max(p["score"] for p in agg)*100+1
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
        rs=by_h[h];x=np.array([r[metric] for r in rs],float);y=np.array([r["score"]*100 for r in rs])
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
    for ax in axes[:,0]:ax.set_ylabel("Task outcome score (%)")
    for ax in axes[-1]:ax.set_xlabel(xlabel)
    handles=[Line2D([0],[0],marker='o',linestyle='',markerfacecolor=c,markeredgecolor='none',label=t,markersize=7) for t,c in TOPIC_COLORS.items()]
    fig.legend(handles=handles,loc="lower center",ncol=4,frameon=False,fontsize=9,bbox_to_anchor=(.5,.045))
    fig.suptitle(f"Task-level score vs. {xlabel.lower()}",x=.06,ha="left",fontsize=22,fontweight="bold")
    fig.text(.06,.93,"Each point is one task · color is declared topic · line is descriptive OLS on log resource use",color=MUTED)
    fig.text(.06,.02,"Correlations are descriptive, not causal: task difficulty influences both score and resource use.",fontsize=9,color=MUTED)
    fig.subplots_adjust(top=.86,bottom=.21,hspace=.3,wspace=.17)
    save(fig,out/filename)


def comparison_markdown(agg):
    lines=["# Harness-Bench comparison table","", "Raw outcome score; 106 tasks per option. Input is standardized as Total − Cache read − Output, so the three displayed token components are non-overlapping and sum to Total.","",
           "| Option | Score | Input tokens | Cache read tokens | Output tokens | Total tokens | Calls | Runtime |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for x in sorted((r for r in agg if r["harness_id"] in MAIN_ORDER),key=lambda r:-r["score"]):
        lines.append(f"| {x['harness']} | {x['score']*100:.2f}% | {x['input_tokens']:,} | {x['cache_read_tokens']:,} | {x['output_tokens']:,} | {x['total_tokens']:,} | {x['calls']:,} | {x['elapsed_seconds']/3600:.2f} h |")
    lines += ["", "Excluded: original Hermes (oracle leakage); Claude Code pilot (systemic tool failures; not a valid baseline).",
              "", "## Hermes profile comparison", "",
              "Contained default is the headline Hermes result. Focused/aggressive were a separate, counterbalanced post-hoc ablation.", "",
              "| Hermes profile | Role | Score | Total tokens | Calls | Runtime |", "|---|---|---:|---:|---:|---:|"]
    for h,role in [("hermes_default","Headline contained rerun"),("hermes_focused","Post-hoc: fixed skills surface"),("hermes_aggressive","Post-hoc: same surface minus skills")]:
        x=next(r for r in agg if r["harness_id"]==h)
        lines.append(f"| {x['harness'].replace('*','')} | {role} | {x['score']*100:.2f}% | {x['total_tokens']:,} | {x['calls']:,} | {x['elapsed_seconds']/3600:.2f} h |")
    lines += ["", "Aggressive minus focused: **+0.21 percentage points**; paired wins/ties/losses **15/67/24**; paired t-test **p=0.861**; task-bootstrap 95% CI approximately **[-2.12, +2.59] points**. No statistically detectable quality difference was observed in this single-run paired comparison, while aggressive used about 40% fewer tokens, 9.9% fewer calls, and 4.9% less runtime."]
    return "\n".join(lines)+"\n"


def render_html(agg):
    table=[]
    for x in sorted((r for r in agg if r["harness_id"] in MAIN_ORDER),key=lambda r:-r["score"]):
        table.append("<tr>"+"".join(f"<td>{v}</td>" for v in [html.escape(x["harness"]),f"{x['score']*100:.2f}%",f"{x['input_tokens']:,}",f"{x['cache_read_tokens']:,}",f"{x['output_tokens']:,}",f"{x['total_tokens']:,}",f"{x['calls']:,}",f"{x['elapsed_seconds']/3600:.2f} h"])+"</tr>")
    hermes_table=[]
    for h,role in [("hermes_default","Headline contained rerun"),("hermes_focused","Post-hoc fixed skills"),("hermes_aggressive","Post-hoc minus skills")]:
        x=next(r for r in agg if r["harness_id"]==h)
        hermes_table.append("<tr>"+"".join(f"<td>{v}</td>" for v in [html.escape(x["harness"].replace("*","")),role,f"{x['score']*100:.2f}%",f"{x['total_tokens']:,}",f"{x['calls']:,}",f"{x['elapsed_seconds']/3600:.2f} h"])+"</tr>")
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Harness-Bench comparison</title><style>
    :root{{--ink:#111;--paper:#fafaf8;--muted:#6f716d;--accent:#85ed75;--line:#d9dad5}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Avenir Next",Inter,system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:54px 28px 90px}}.eyebrow{{font:12px ui-monospace,monospace;letter-spacing:.12em}}h1{{font-size:54px;line-height:.98;max-width:850px;margin:18px 0}}.lede{{font-size:20px;color:var(--muted);max-width:840px}}.rule{{height:8px;background:var(--accent);width:128px;margin:28px 0 60px}}section{{margin:70px 0}}h2{{font-size:30px;margin-bottom:8px}}p.note{{color:var(--muted);max-width:900px}}img{{width:100%;display:block;border:1px solid var(--line);background:var(--paper)}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{padding:13px 10px;border-bottom:1px solid var(--line);text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{font-size:12px;text-transform:uppercase;letter-spacing:.06em}}.callout{{border-left:8px solid var(--accent);padding:5px 20px}}code{{font-family:ui-monospace,monospace}}@media(max-width:700px){{h1{{font-size:38px}}.wide{{overflow-x:auto}}}}
    </style></head><body><main><div class="eyebrow">HARNESS-BENCH / INTERIM AUDITED COMPARISON</div><h1>Quality and efficiency across agent harnesses</h1><p class="lede">A like-for-like comparison on 106 real-workspace tasks using GPT-5.4 medium. This interim edition includes only complete, audited datasets.</p><div class="rule"></div>
    <section><h2>Overall quality</h2><p class="note">Raw outcome score is used because full-trace process grading is not yet available for every option.</p><img src="figures/overall_quality.svg" alt="Overall quality bar chart"></section>
    <section><h2>Score, tokens, and runtime</h2><div class="wide"><table><thead><tr><th>Option</th><th>Score</th><th>Input tokens</th><th>Cache read tokens</th><th>Output tokens</th><th>Total tokens</th><th>Calls</th><th>Runtime</th></tr></thead><tbody>{''.join(table)}</tbody></table></div><p class="note">Input is standardized as Total − Cache read − Output, so Input, Cache read, and Output are non-overlapping and sum to Total. This corrects Codex’s native convention, where reported input includes cache reads. Reasoning tokens are already contained in output and are not added again. Runtime is summed task elapsed time.</p></section>
    <section class="callout"><h2>Audited composition</h2><p><strong>Codex:</strong> 105 entries from the initial full manifest plus the second, valid operational correction for task 023. The initial task and failed first correction remain preserved and excluded.</p></section>
    <section><h2>Quality by topic</h2><img src="figures/topic_quality_facets.svg" alt="Quality by task topic"></section>
    <section><h2>Quality–efficiency frontier</h2><img src="figures/quality_efficiency_frontier.svg" alt="Quality efficiency frontier"></section>
    <section><h2>Task-level detail</h2><p class="note">The regression lines are descriptive only; task difficulty affects both score and resource use.</p><img src="figures/task_tokens_facets.svg" alt="Task score versus tokens"><br><img src="figures/task_runtime_facets.svg" alt="Task score versus runtime"></section>
    <section><h2>Hermes profile note</h2><p class="note">Only contained benchmark-default Hermes appears in the headline charts. The other profiles came from a separate counterbalanced post-hoc ablation.</p><div class="wide"><table><thead><tr><th>Hermes profile</th><th>Role</th><th>Score</th><th>Total tokens</th><th>Calls</th><th>Runtime</th></tr></thead><tbody>{''.join(hermes_table)}</tbody></table></div><p>No statistically detectable quality difference was observed between focused and aggressive in this single-run paired comparison: aggressive minus focused was <strong>+0.21 points</strong> (wins/ties/losses 15/67/24; paired t-test p=0.861; task-bootstrap 95% CI ≈ [-2.12, +2.59] points). Aggressive used about <strong>40% fewer tokens</strong>, 9.9% fewer calls, and 4.9% less runtime.</p><p class="note">Six aggressive cells (091, 094, 096, 097, 099, 106) use separately preserved corrections after a terminal audit classified their originals as watchdog-truncated infrastructure failures.</p></section>
    <section class="callout"><h2>Scope and exclusions</h2><p><strong>Original Hermes is excluded</strong> after confirmed access to oracle/ground-truth material. The contained benchmark-default rerun is the Hermes headline result.</p><p><strong>The Claude Code pilot is not a baseline.</strong> Nine completed tasks averaged 9.3%, but all 51 Bash calls failed before shell startup with <code>E2BIG</code>, and all 30 Write plus 8 Edit calls were policy-denied. With no mutation channel, the tasks were technically impossible to complete. The 2.08M standardized model-context tokens were retry-contaminated and only 11% above default Hermes on the same nine tasks, so they do not establish normal Claude inefficiency. Any future attempt must first pass an exact production Write/Edit/Bash containment canary and a single-task smoke. Tasks 008 and 013 also carry the benchmark’s documented oracle-quality-LLM comparability caveat.</p></section>
    <section><p class="eyebrow">GENERATED FROM AUDITED PER-TASK ARTIFACTS · SNAPSHOT DATA INCLUDED</p></section></main></body></html>'''


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
        if not (abs(x["score"]-score)<5e-10 and x["total_tokens"]==tokens and
                abs(x["elapsed_seconds"]-elapsed)<.02 and x["calls"]==calls and
                x["reported_input_tokens"]==input_tokens and x["cache_read_tokens"]==cache_read and
                x["output_tokens"]==output_tokens and x["cache_write_tokens"]==0 and
                x["input_tokens"] + x["cache_read_tokens"] + x["output_tokens"] == x["total_tokens"]):
            raise RuntimeError(f"published aggregate drift for {x['harness_id']}: {x}")
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