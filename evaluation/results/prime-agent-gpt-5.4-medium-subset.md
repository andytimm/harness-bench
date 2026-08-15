# Prime Agent + GPT-5.4 medium: 47-task subset

Run date: 2026-08-15  
Harness-Bench base commit: `1025086a446653702b80cfb48babbeec35db6b2c`  
Prime Agent version: `0.7.2`  
Prime invocation: subscription-authenticated `openai-codex/gpt-5.4`, thinking `medium`

## Method

The subset was frozen before additional subset execution as the union of three upstream task classes: Software Engineering & Codebase Maintenance, Data, BI & Finance Analytics, and Long-running Autonomy & State Adaptation. This produces 47 tasks. Six valid pilot results in the subset were reused; the remaining 41 tasks were run sequentially. Process grading and oracle-quality LLM grading were intentionally skipped, so the figures below are deterministic oracle outcome scores rather than standardized combined/process scores. Raw Prime JSONL, native session files, usage-proxy traces, sandboxes, and result JSON were retained locally.

## Aggregate

- Mean oracle outcome: **0.8476**; median: **0.9093**.
- Adapter completion: **47/47**.
- Perfect outcomes: **13/47**; **24/47** outcomes were at least 0.9; **2/47** were below 0.6.
- Sum of per-task wall time: **3,631.658 seconds** (60.53 minutes); mean **77.27s**; median **74.83s**.
- Native usage: **373 model calls** over **59 benchmark rounds**.
- Tokens: **696,108 uncached input**, **2,335,744 cache-read**, **168,750 output**, **3,200,602 total** as reported by Prime. Mean total was **68,098/task**; median **53,957/task**.

## Category results

| Category | Tasks | Mean outcome | Median | Perfect | Wall time | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| Data, BI & Finance Analytics | 14 | 0.8308 | 0.8462 | 4 | 943.821s | 802,908 |
| Long-running Autonomy & State Adaptation | 11 | 0.8718 | 0.8594 | 4 | 932.457s | 609,784 |
| Software Engineering & Codebase Maintenance | 22 | 0.8461 | 0.9439 | 5 | 1755.380s | 1,787,910 |

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
| `040-test-coverage-fill` | Software engineering | 0.4300 | 79.331s | 12 | 1 | 89,643 |
| `041-frontend-state-bug` | Software engineering | 0.9812 | 72.791s | 4 | 1 | 32,012 |
| `042-api-schema-migration` | Software engineering | 0.6174 | 124.304s | 10 | 1 | 106,119 |
| `043-db-migration-safety` | Software engineering | 0.9950 | 178.358s | 12 | 1 | 177,631 |
| `044-ci-config-repair` | Software engineering | 0.7000 | 47.163s | 8 | 1 | 53,957 |
| `045-dependency-upgrade-compat` | Software engineering | 0.7500 | 89.234s | 13 | 1 | 105,120 |
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
| `082-compose-config-repair` | Software engineering | 0.7000 | 46.721s | 9 | 1 | 69,846 |
| `083-monorepo-interface-repair` | Software engineering | 1.0000 | 44.809s | 10 | 1 | 74,803 |
| `084-js-state-type-bug` | Software engineering | 0.9938 | 46.738s | 7 | 1 | 49,390 |
| `085-flaky-test-root-cause` | Software engineering | 0.6200 | 74.830s | 15 | 1 | 119,417 |
| `086-sql-migration-preflight-rollback` | Software engineering | 0.9784 | 84.324s | 4 | 1 | 34,678 |
| `087-cli-parser-bug-tests` | Software engineering | 0.8964 | 105.224s | 13 | 1 | 112,536 |
| `088-api-contract-mock-client-compat` | Software engineering | 0.6000 | 106.317s | 14 | 1 | 134,555 |
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
- The repository does not declare `pytest`, although multiple oracles and task agents invoke it. During the pilot, `016` could not be scored and `083` was incorrectly penalized in this deficient environment. Pytest 9.1.1 was installed and the venv bin directory was prepended to `PATH`; these two tasks were rerun once as documented integration corrections. Original artifacts were retained.
- `088-api-contract-mock-client-compat` initially stopped in its pre-agent hook because no public tunnel was configured. Prime tools share the hook host network namespace, so the runner mapped the public URL template to the loopback mock service. The setup failure made no model call; the subsequent single model run is the retained result.
- No task was rerun because of a low oracle score. The run stopped on integration failures rather than silently continuing under a broken environment.
- Process/security rubric LLM scoring was skipped. Therefore the synthetic traces are suitable for future inspection, but this report makes no standardized process-score or rubric-derived security claim.

## Comparison status

The pinned upstream checkout includes a Codex GPT-5.4 medium configuration but no committed per-task result artifacts or published aggregate table. A direct Codex comparison should wait until the matching published task-level data and settings are identified; no Codex rerun was performed here.
