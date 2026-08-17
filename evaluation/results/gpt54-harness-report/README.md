# Harness-Bench: audited harness comparison

An interim, shareable comparison of six complete GPT-5.4-medium configurations across the same 106 real-workspace tasks. Open [`index.html`](index.html) for the polished report.

![Overall raw outcome quality](figures/overall_quality.svg)

## Included options

- **Prime Agent**
- **Pi**
- **Codex CLI 0.139**, with the audited operational replacement for task 023
- **Hermes — contained benchmark default**, the headline Hermes result
- **Hermes — focused with skills**, a post-hoc fixed-profile ablation
- **Hermes — aggressive without skills**, a post-hoc fixed-profile ablation with six audited watchdog corrections

The fixed Hermes profiles are shown for analysis but are never pooled with, or substituted for, benchmark-default Hermes.

## Comparison table

Raw outcome score is the common `scoring.outcome_score`. Token fields follow each native usage source and may overlap; **Total tokens** is the cross-harness comparison field.

| Option | Score | Reported input | Reported cache read | Output | Total tokens | Calls | Runtime |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prime Agent | 86.65% | 1,387,583 | 3,663,872 | 314,356 | 5,365,811 | 670 | 1.87 h |
| Pi | 86.12% | 853,187 | 1,489,152 | 308,079 | 2,650,418 | 558 | 1.95 h |
| Hermes — aggressive* | 85.30% | 1,808,935 | 7,692,672 | 544,425 | 10,046,032 | 921 | 3.27 h |
| Hermes — focused* | 85.09% | 2,502,152 | 13,687,296 | 573,363 | 16,762,811 | 1,022 | 3.44 h |
| Hermes — default | 84.51% | 2,292,231 | 18,115,968 | 567,375 | 20,975,574 | 966 | 3.40 h |
| Codex CLI 0.139 | 80.26% | 15,304,660 | 13,760,384 | 505,527 | 15,810,187 | 649 | 2.92 h |

\* Post-hoc ablation. Runtime is summed task elapsed time, not end-to-end wall-clock.

## Figures

- [`overall_quality.svg`](figures/overall_quality.svg) — headline quality with 95% task-bootstrap intervals
- [`topic_quality_facets.svg`](figures/topic_quality_facets.svg) — quality across the eight declared task classes
- [`quality_efficiency_frontier.svg`](figures/quality_efficiency_frontier.svg) — aggregate score against tokens and runtime
- [`task_tokens_facets.svg`](figures/task_tokens_facets.svg) — task-level score against tokens, colored by task class
- [`task_runtime_facets.svg`](figures/task_runtime_facets.svg) — task-level score against runtime, colored by task class

PNG versions are provided for slides and social sharing; SVG versions are preferred for documents.

Normalized snapshots live in [`data/`](data/): task metadata, 636 authoritative task-level rows, aggregates, topic summaries, exclusions, and the hashed provenance manifest. These make chart iteration possible without copying raw run sandboxes.

## Scope and exclusions

- **Original Hermes is excluded.** Four tasks (037, 046, 055, and 079) accessed oracle or ground-truth material. Only the separately contained rerun and audited ablations appear here.
- **Claude Code Opus 4.6 is pending.** Its sequential evaluation is incomplete, and quota-censored partial work is non-authoritative. It will be added only after completion and audit.
- All headline scores are raw outcomes. Prime and Pi have full-trace-v2 process grades, but those are not mixed into this report because equivalent grades are not yet available for Codex and contained/fixed Hermes.
- Tasks 008 and 013 retain the benchmark's documented oracle-quality-LLM comparability caveat.
- Bootstrap intervals quantify sensitivity to the sampled task set, **not** run-to-run model variance; each configuration has one retained attempt per task, apart from explicitly documented operational replacements.
- Task-level regression lines are descriptive OLS fits on log resource use. They are not causal: task difficulty affects both score and resource use.

## Audited composition

- **Codex:** 105 entries from the initial full manifest plus only the second operational correction for task 023. The initial task and failed first correction remain preserved but are excluded.
- **Aggressive Hermes:** 100 initial fixed-profile results plus corrections for tasks 091, 094, 096, 097, 099, and 106, which were independently classified as watchdog-truncated infrastructure failures before correction scoring.
- **Categories:** the `class` field in each `tasks/<task>/task.yaml`; no category is inferred from task number or name.

[`data/provenance.json`](data/provenance.json) records SHA-256 hashes for every raw result or audit input, including the four retained original-Hermes records supporting the exclusion. The generator also hard-fails if any published aggregate differs from the audited toplines.

## Reproduce

From the repository root:

```bash
MPLBACKEND=Agg .venv/bin/python evaluation/results/gpt54-harness-report/generate_report.py
```

The generator reads audited local/external run roots, writes normalized CSV snapshots, and regenerates every PNG, SVG, Markdown table, and the HTML report. It does not launch models or modify evaluation roots.

Full raw-source regeneration requires the original ignored `data_try6`, `reports`, `evaluation/runs`, evaluation-gate, and `~/.harnessbench/runs` artifacts; those large/private run roots are not distributed here. External recipients can inspect and reuse the included normalized snapshots and compare the accompanying raw-input inventory in `data/provenance.json`, but the current generator does not rebuild figures from snapshots alone.

## Design

The visual system takes restrained inspiration from Prime Intellect's current editorial language: warm neutral paper, dark ink, compact mono labels, and a single highlighter-green accent. It deliberately avoids logos or close brand imitation.
