# Prime Agent + GPT-5.4 medium: 47-task subset

Run date: 2026-08-15  
Harness-Bench base commit: `1025086a446653702b80cfb48babbeec35db6b2c`  
Prime Agent version: `0.7.2`  
Prime invocation: subscription-authenticated `openai-codex/gpt-5.4`, thinking `medium`

## Method

The subset was frozen before additional subset execution as the union of three upstream task classes: Software Engineering & Codebase Maintenance, Data, BI & Finance Analytics, and Long-running Autonomy & State Adaptation. This produces 47 tasks. Six valid pilot results in the subset were reused; the remaining tasks were run sequentially. Process grading and oracle-quality LLM grading were intentionally skipped, so the figures below are deterministic oracle outcome scores rather than standardized combined/process scores. Raw Prime JSONL, native session files, usage-proxy traces, sandboxes, and result JSON were retained locally.

## Aggregate

- Mean oracle outcome: **0.8873**; median: **0.9800**.
- Adapter completion: **47/47**.
- Perfect outcomes: **16/47**; **29/47** outcomes were at least 0.9; **1/47** was below 0.6.
- Sum of retained per-task wall time: **3,500.357 seconds** (58.34 minutes); mean **74.48s**; median **69.15s**.
- Native usage: **343 model calls** over **59 benchmark rounds**.
- Tokens: **681,775 uncached input**, **2,067,968 cache-read**, **163,102 output**, **2,912,845 total** as reported by Prime. Mean total was **61,975/task**; median **51,222/task**.

## Category results

| Category | Tasks | Mean outcome | Median | Perfect | Mean time/task | Mean tokens/task |
|---|---:|---:|---:|---:|---:|---:|
| Data, BI & Finance Analytics | 14 | 0.8308 | 0.8462 | 4 | 67.4s | 57,351 |
| Long-running Autonomy & State Adaptation | 11 | 0.8718 | 0.8594 | 4 | 84.8s | 55,435 |
| Software Engineering & Codebase Maintenance | 22 | 0.9310 | 0.9827 | 8 | 73.8s | 68,189 |

## Per-task results

| Task | Category | Outcome | Wall time | Calls | Rounds | Total tokens |
|---|---|---:|---:|---:|---:|---:|
| `007-session-memory` | Long-running autonomy | 1.0000 | 17.539s | 4 | 2 | 20,280 |
| `009-git-pr-merge` | Software engineering | 1.0000 | 18.279s | 3 | 1 | 16,048 |
| `011-code-debug` | Software engineering | 0.8650 | 90.744s | 22 | 5 | 164,120 |
| `014-task-decomposition` | Long-running autonomy | 0.7975 | 74.669s | 5 | 1 | 38,862 |
| `016-code-repair-pytest` | Software engineering | 1.0000 | 24.191s | 6 | 1 | 37,935 |
| `017-db-doc-consistency` | Software engineering | 1.0000 | 87.697s | 8 | 1 | 92,171 |
| `018-provider-failover-audit` | Software engineering | 0.9093 | 135.314s | 13 | 1 | 128,864 |
| `039-repo-architecture-map` | Software engineering | 0.9785 | 128.953s | 5 | 1 | 52,430 |
| `040-test-coverage-fill` | Software engineering | 1.0000 | 59.331s | 7 | 1 | 53,064 |
| `041-frontend-state-bug` | Software engineering | 0.9812 | 72.791s | 4 | 1 | 32,012 |
| `042-api-schema-migration` | Software engineering | 0.7400 | 128.371s | 6 | 1 | 68,150 |
| `043-db-migration-safety` | Software engineering | 0.9950 | 178.358s | 12 | 1 | 177,631 |
| `044-ci-config-repair` | Software engineering | 1.0000 | 40.203s | 7 | 1 | 44,377 |
| `045-dependency-upgrade-compat` | Software engineering | 0.9800 | 59.236s | 7 | 1 | 51,222 |
| `046-performance-regression` | Software engineering | 1.0000 | 33.765s | 5 | 1 | 27,039 |
| `047-code-review-risk-report` | Software engineering | 0.6150 | 48.112s | 3 | 1 | 21,716 |
| `048-release-note-changelog` | Software engineering | 0.9842 | 88.181s | 9 | 1 | 87,880 |
| `049-excel-like-cleaning` | Data/BI | 1.0000 | 88.199s | 9 | 1 | 83,369 |
| `050-multitable-join-analysis` | Data/BI | 1.0000 | 90.291s | 5 | 1 | 51,506 |
| `051-sql-query-report` | Data/BI | 1.0000 | 46.174s | 6 | 1 | 42,519 |
| `052-metric-definition-audit` | Data/BI | 0.6900 | 44.626s | 6 | 1 | 43,208 |
| `053-anomalous-transaction-detect` | Data/BI | 0.9786 | 43.604s | 6 | 1 | 47,182 |
| `054-budget-variance-analysis` | Data/BI | 1.0000 | 62.144s | 9 | 1 | 70,739 |
| `055-funnel-dropoff-analysis` | Data/BI | 0.9836 | 76.170s | 10 | 1 | 93,223 |
| `056-inventory-forecast` | Data/BI | 0.6900 | 34.580s | 7 | 1 | 46,298 |
| `057-interruption-resume` | Long-running autonomy | 0.7308 | 53.109s | 6 | 2 | 42,079 |
| `058-multiday-project-state` | Long-running autonomy | 0.8594 | 165.997s | 10 | 3 | 104,477 |
| `059-event-update-replan` | Long-running autonomy | 1.0000 | 62.740s | 6 | 2 | 43,494 |
| `060-task-cancellation-cleanup` | Long-running autonomy | 1.0000 | 44.161s | 6 | 2 | 39,302 |
| `061-periodic-status-rollup` | Long-running autonomy | 1.0000 | 83.300s | 5 | 1 | 34,030 |
| `082-compose-config-repair` | Software engineering | 0.9800 | 41.640s | 6 | 1 | 44,004 |
| `083-monorepo-interface-repair` | Software engineering | 1.0000 | 44.809s | 10 | 1 | 74,803 |
| `084-js-state-type-bug` | Software engineering | 0.9938 | 46.738s | 7 | 1 | 49,390 |
| `085-flaky-test-root-cause` | Software engineering | 1.0000 | 55.217s | 11 | 1 | 81,523 |
| `086-sql-migration-preflight-rollback` | Software engineering | 0.9784 | 84.324s | 4 | 1 | 34,678 |
| `087-cli-parser-bug-tests` | Software engineering | 0.8821 | 58.924s | 8 | 1 | 65,116 |
| `088-api-contract-mock-client-compat` | Software engineering | 0.6000 | 98.901s | 12 | 1 | 95,980 |
| `089-ab-test-caveat-analysis` | Data/BI | 0.9524 | 68.651s | 10 | 1 | 71,478 |
| `090-timeseries-anomaly-attribution` | Data/BI | 0.6271 | 61.651s | 6 | 1 | 50,498 |
| `091-financial-close-reconciliation` | Data/BI | 0.6393 | 79.193s | 7 | 1 | 61,681 |
| `092-schema-drift-audit` | Data/BI | 0.7400 | 107.228s | 4 | 1 | 42,547 |
| `093-jsonl-sessionization-analysis` | Data/BI | 0.5918 | 72.156s | 5 | 1 | 41,311 |
| `094-metric-definition-migration-diff` | Data/BI | 0.7391 | 69.154s | 7 | 1 | 57,349 |
| `103-policy-update-replan-diff` | Long-running autonomy | 0.9850 | 139.963s | 7 | 2 | 83,494 |
| `104-async-ops-window-rollup` | Long-running autonomy | 0.7975 | 134.365s | 11 | 1 | 99,071 |
| `105-partial-batch-resume-ledger` | Long-running autonomy | 0.7156 | 80.314s | 7 | 2 | 68,354 |
| `106-release-approval-gate-plan` | Long-running autonomy | 0.7045 | 76.300s | 4 | 1 | 36,341 |

## Validation and integration notes

- All 47 retained final results report adapter success, `openai-codex` / `gpt-5.4`, Prime `0.7.2`, final stop reason `stop`, readable native session files, and no proxy-trace extraction error.
- Staged OAuth credentials were removed from every retained final sandbox. A conservative string audit of raw stdout found no references to `ground_truth.json`, `oracle_grade.py`, or repository `tasks/` paths. This supports the leakage check but is not an OS-level isolation guarantee.
- Several tasks require undeclared `pytest`; one validator also requires PyYAML. The first attempted runner correction mistakenly used `Path(sys.executable).resolve().parent`, which followed the venv interpreter symlink into uv’s base Python directory instead of preserving `.venv/bin`. Analysis caught dependency errors in six oracle results and eight trajectories. The runner now uses the un-resolved interpreter parent, rejects retained results containing known dependency failures, and the eight affected tasks (`040`, `042`, `044`, `045`, `082`, `085`, `087`, `088`) were rerun once with the intended environment. Invalid result JSON and original sandboxes were retained locally.
- `088-api-contract-mock-client-compat` had earlier stopped in its pre-agent hook because no public tunnel was configured. Prime tools share the hook host network namespace, so the runner maps the public URL template to the loopback mock service. That setup failure made no model call.
- No task was rerun because of a low oracle score. Every rerun above corrected a documented integration defect that affected the environment available during agent execution, not merely retrospective grading.
- Process/security rubric LLM scoring was skipped. Therefore the synthetic traces are suitable for future inspection, but this report makes no standardized process-score or rubric-derived security claim.

## Published comparison

The authors’ public `leaderboard_scores.json` contains exact per-task GPT-5.4 completion scores for all seven published harness configurations. On these same 47 task IDs, Prime scored **0.8873** versus published Codex **0.8880**, a difference of **−0.0007**. Prime’s software mean was **0.9310** versus Codex **0.9312**; data/BI **0.8309** versus **0.8459**; long-running autonomy **0.8718** versus **0.8553**. See `prime-agent-gpt-5.4-comparison.md` and the accompanying plots.

The comparison is strong but not a perfect controlled ablation: the published file records `gpt-5.4` but not effort, backend revision, harness versions, or benchmark SHA. The released Codex config uses medium reasoning and all 47 task IDs/categories match, but artifact-level equivalence cannot be proven.
