# Prime Agent Evaluation Notes

## Scope and commits

- Harness-Bench upstream: `Qihoo360/harness-bench` at `1025086a446653702b80cfb48babbeec35db6b2c` (Harness-Bench 2.0, 106 tasks).
- Working fork: `andytimm/harness-bench`, branch `feat/prime-agent-adapter`.
- Prime Agent initially installed at version `0.7.2`.
- Current upstream has no conventional LICENSE file. Treat upstream contribution and redistribution questions cautiously.

## Methodological decisions (made before task results)

- Compatibility/control run model: `openai-codex/gpt-5.4` through the existing OpenAI subscription OAuth used by Prime Agent.
- Reasoning effort: `medium`. This exactly matches the model and reasoning effort in Harness-Bench's current example Codex configuration, enabling the cleanest available harness comparison.
- After the Prime + GPT-5.4 validation and pilot, run Prime + `openai-codex/gpt-5.6-sol` at `medium` as a newer-model extension. This order was changed before any benchmark task was run.
- Prime will be invoked noninteractively with structured JSON output and a task-local session/state directory.
- Preserve Prime's native event stream and session transcript even when it cannot be routed through Harness-Bench's usage proxy.
- Prioritize deterministic oracle/outcome grading. Set `HARNESSBENCH_SKIP_PROCESS_GRADE=1` for initial validation and pilot so process grading neither requires a paid rubric API nor conflates native traces with standardized proxy traces.
- Do not retry failed benchmark attempts merely to improve scores. Integration failures discovered during validation may be fixed and rerun, with both the failure and rationale retained in notes/results.
- No paid API route will be introduced without a separate justification and user discussion.

## Validation and pilot selection

One-task integration validation:

1. `001-file` — minimal workspace, prompt, artifact, and deterministic oracle validation.

Pilot tasks selected before observing benchmark outcomes:

1. `016-code-repair-pytest` — ordinary Python repair and test verification.
2. `039-repo-architecture-map` — repository navigation and code/document synthesis.
3. `050-multitable-join-analysis` — data/BI work across joined tables.
4. `022-local-rest-api-summary` — local service/tool use and data processing.
5. `010-office-docs` — CSV/PDF ingestion and DOCX/JSON artifact creation.
6. `057-interruption-resume` — two-round interruption/resume and state management.
7. `083-monorepo-interface-repair` — cross-package coding and pytest verification.
8. `104-async-ops-window-rollup` — asynchronous injections, polling, deduplication, and long-running state.

This pilot deliberately spans coding, repository understanding, data/BI, local tools/services, office artifacts, multi-round state, and long-running behavior. The selected list will not be changed based on performance.

## Known instrumentation distinction

Harness-Bench normally computes standardized process traces from its usage proxy. Subscription-backed Prime Agent calls the OpenAI Codex product route directly and exposes its own native structured events/session logs. The minimum adapter will therefore:

1. validate outcome scoring through the normal workspace oracle;
2. save all Prime-native events and usage metadata;
3. synthesize a proxy-compatible trace only if the native schema supports a faithful mapping;
4. avoid claiming directly comparable process metrics until that mapping is validated.

## Running observations

- `config/harness.example.yaml` is YAML rather than strict JSON (it includes a trailing comma), so the declared PyYAML dependency must be installed. A repository-local `.venv` was created with `uv` and the project installed editable.

## One-task validation result

Run completed on 2026-08-15 with Prime Agent 0.7.2, `openai-codex/gpt-5.4`, thinking `medium`, and subscription OAuth.

- Task: `001-file`
- Adapter status: success
- Oracle outcome: `1.0` (the only check passed)
- Wall time: `5.02s`
- Native model calls: 2
- Usage: 4,540 uncached input tokens, 4,096 cache-read tokens, 94 output tokens, 8,730 total tokens as reported by Prime
- Trajectory: one IPython call read `in/input.txt`, counted four lines, and wrote only `out/linecount.txt`; the final response was `Done.`
- Manual checks: fixture unchanged, output was exactly `4`, prompt used the intended workspace, no grader/ground-truth path appeared in the trajectory, raw JSONL and native session JSONL were retained, synthetic trace extraction was coherent, and the staged OAuth credential was removed from the retained sandbox.
- Process grading was intentionally skipped according to the precommitted outcome-first plan.

Isolation limitation: Harness-Bench copies only fixtures into the task workspace, so graders and ground truth are not present there. Prime's local Python/tool process is not an OS-level filesystem sandbox, however, and the default Harness-Bench work root is inside the repository. A sufficiently exploratory or adversarial agent could navigate outside the workspace. The validation trajectory did not do so. This limitation should be reported and compared with the effective filesystem permissions of other local adapters rather than described as hard isolation.

## GPT-5.4 pilot result

The eight precommitted pilot tasks completed with valid outcome scores after correcting one benchmark-environment issue:

| Task | Outcome | Wall time (s) | Total tokens |
|---|---:|---:|---:|
| `016-code-repair-pytest` | 1.0000 | 24.191 | 37,935 |
| `039-repo-architecture-map` | 0.9785 | 128.953 | 52,430 |
| `050-multitable-join-analysis` | 1.0000 | 90.291 | 51,506 |
| `022-local-rest-api-summary` | 0.8696 | 79.960 | 57,653 |
| `010-office-docs` | 1.0000 | 49.604 | 69,411 |
| `057-interruption-resume` | 0.7308 | 53.109 | 42,079 |
| `083-monorepo-interface-repair` | 1.0000 | 44.809 | 74,803 |
| `104-async-ops-window-rollup` | 0.7975 | 134.365 | 99,071 |

Aggregate:

- Mean oracle outcome: **0.9220**; four of eight tasks scored 1.0.
- All eight final adapter runs completed successfully; no timeout or provider failure.
- Total wall time: **605.282s** (10.1 minutes); mean 75.66s/task; median 66.53s/task.
- Total reported tokens: **484,888**; mean 60,611/task; median 55,041.5/task.
- Nine task rounds produced 62 model calls. Input totals distinguish uncached input from cache reads in Prime's native usage.
- Naive pilot-based estimates: about 59 minutes / 2.85M tokens for 47 tasks, or 134 minutes / 6.42M tokens for 106 tasks. These estimates are uncertain because asynchronous and high-timeout task mixes differ.

Benchmark environment correction: the repository declares no `pytest` dependency, although multiple oracles invoke `pytest` or `python3 -m pytest`. The initial `016` run completed at the adapter level but its oracle crashed because `pytest` was missing, so no result was written. The initial `083` run was incorrectly penalized for the same missing dependency. After installing pytest 9.1.1 and prepending the benchmark venv's bin directory to `PATH` (equivalent to activation), both tasks were rerun once as integration corrections. The invalid `083` result and both original sandboxes were retained locally. These were not retries of model-scored failures.

## Predefined 47-task decision-relevant subset

The handoff's anticipated 47-task subset exactly matches the union of three upstream `task.yaml` classes at the pinned commit:

1. `Software Engineering & Codebase Maintenance` — 22 tasks
2. `Data, BI & Finance Analytics` — 14 tasks
3. `Long-running Autonomy & State Adaptation` — 11 tasks

This class-based selection rule was fixed before running any additional subset tasks and will not be altered based on pilot performance. The resulting task IDs are:

`007-session-memory`, `009-git-pr-merge`, `011-code-debug`, `014-task-decomposition`, `016-code-repair-pytest`, `017-db-doc-consistency`, `018-provider-failover-audit`, `039-repo-architecture-map`, `040-test-coverage-fill`, `041-frontend-state-bug`, `042-api-schema-migration`, `043-db-migration-safety`, `044-ci-config-repair`, `045-dependency-upgrade-compat`, `046-performance-regression`, `047-code-review-risk-report`, `048-release-note-changelog`, `049-excel-like-cleaning`, `050-multitable-join-analysis`, `051-sql-query-report`, `052-metric-definition-audit`, `053-anomalous-transaction-detect`, `054-budget-variance-analysis`, `055-funnel-dropoff-analysis`, `056-inventory-forecast`, `057-interruption-resume`, `058-multiday-project-state`, `059-event-update-replan`, `060-task-cancellation-cleanup`, `061-periodic-status-rollup`, `082-compose-config-repair`, `083-monorepo-interface-repair`, `084-js-state-type-bug`, `085-flaky-test-root-cause`, `086-sql-migration-preflight-rollback`, `087-cli-parser-bug-tests`, `088-api-contract-mock-client-compat`, `089-ab-test-caveat-analysis`, `090-timeseries-anomaly-attribution`, `091-financial-close-reconciliation`, `092-schema-drift-audit`, `093-jsonl-sessionization-analysis`, `094-metric-definition-migration-diff`, `103-policy-update-replan-diff`, `104-async-ops-window-rollup`, `105-partial-batch-resume-ledger`, `106-release-approval-gate-plan`.
