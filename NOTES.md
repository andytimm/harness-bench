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
