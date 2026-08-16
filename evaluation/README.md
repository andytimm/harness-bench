# Prime Agent evaluation helpers

The pilot task list was committed in `NOTES.md` before benchmark results were observed and is duplicated as a constant in `run_prime_pilot.py`.

From the repository root, after installing the project and the benchmark oracles' undeclared `pytest` requirement into `.venv`:

```bash
uv pip install --python .venv/bin/python -e . pytest
PYTHONPATH=src .venv/bin/python evaluation/run_prime_pilot.py
```

The runner prepends `.venv/bin` to `PATH`, matching an activated virtual environment. This matters because several benchmark oracles invoke `python3` or `pytest` by executable name.

The script runs tasks sequentially, skips paid/LLM process and oracle-quality grading, refuses to overwrite existing results by default, and writes a local run manifest under `reports/` (ignored by Git). Raw results and sandboxes use `config/app.yaml`.

Summarize completed results without model calls:

```bash
PYTHONPATH=src .venv/bin/python evaluation/summarize_results.py --selection pilot
PYTHONPATH=src .venv/bin/python evaluation/summarize_results.py --selection subset
```

The relevant subset is derived from the three upstream task classes recorded in `NOTES.md`, yielding 47 tasks at the pinned benchmark commit.

Run the frozen 47-task GPT-5.4 subset sequentially, reusing only validated existing results:

```bash
PYTHONPATH=src .venv/bin/python evaluation/run_prime_subset.py
```

The runner writes its manifest atomically after every task and stops on the first command or validation failure. Rerunning it resumes from validated result JSON files, so completed tasks are not repeated. For local-service tasks it maps the benchmark's public-URL template to the hook's loopback URL: Prime tools share the hook's host network namespace, so this avoids unnecessarily exposing fixture services through a public tunnel.

Run the complete 106-task suite, reusing every validated pilot/subset result:

```bash
PYTHONPATH=src .venv/bin/python evaluation/run_prime_full.py --preflight-only
PYTHONPATH=src .venv/bin/python evaluation/run_prime_full.py
```

The full runner preflights all retained results before making a model call, preserves the corrected virtualenv and loopback-service environment, runs sequentially, and atomically checkpoints after each task. It stops rather than overwriting or silently accepting any invalid result.

Generate the exact-subset comparison plots from retained Prime result JSON and the derived authors' GPT-5.4 score artifact:

```bash
uv pip install --python .venv/bin/python matplotlib
.venv/bin/python evaluation/plot_prime_comparison.py
```

The comparison uses completion/oracle outcome only. The public baseline records exact task and model labels but omits reasoning effort, backend revision, harness versions, and benchmark SHA; see the comparison report for caveats.


## Claude Code Opus 4.6 staged evaluation

`run_claude_full.py` pins Claude Code `2.1.227 (Claude Code)`, binary SHA256
`7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7`, model
`claude-opus-4-6`, and effort `medium`. The committed offline tests make no auth
or model calls.

Provision a dedicated mode-0700 directory containing a mode-0600
`.credentials.json` with subscription OAuth. It must not be `~/.claude`, and no
component copies credentials from the normal profile. This directory is the one
stable canonical `CLAUDE_CONFIG_DIR` and `CLAUDE_SECURESTORAGE_CONFIG_DIR` for
the entire run. On macOS 2.1.227 its Keychain service is
`Claude Code-credentials-<first 8 hex of SHA256(NFC(absolute config path))>`.
A single process lock covers each invocation and refresh, so the harness does
**not** create per-task Keychain namespaces. The adapter does not claim that
removing a file proves Keychain deletion; no ephemeral Keychain item is created
or cleanup attempted. Refreshed plaintext fallback credentials remain in the
dedicated namespace.

Inspect the SHA/task/prompt/fixture/oracle-bound immutable plan offline:

```sh
.venv/bin/python evaluation/run_claude_full.py \
  --run-root /absolute/external/harnessbench-claude-opus46 \
  --tranche 1 --dry-plan
```

Live execution requires a clean planned Git SHA plus `--live` and the literal
acknowledgement printed by `--help`. Tranche 1 is the 53 odd task IDs and tranche
2 the 53 even IDs. Claims precede launch. Claims, results and receipts are
content-hashed and bound to the plan digest; resume revalidates every artifact.
A claim without a receipt is never retried automatically, no result is
overwritten, and a rejected quota event writes a censored receipt and stops.

Offline-proven controls include exact arguments (`--safe-mode`, `--no-chrome`,
empty setting sources, strict empty MCP, disabled slash commands), settings
schema fixtures, environment scrubbing, path ancestor/mode checks, home/control
plane Seatbelt profile generation, immutable planning/resume validation, quota
stop logic, and malformed/out-of-order/duplicate/realistic transcript parsing.
The lossless normalized trace preserves raw thinking, tool use/results, errors,
UUIDs, session, round, resume and actual model; the native transcript and both
hashes are retained.

A launch remains blocked unless the macOS Seatbelt capability smoke passes.
Before paid evaluation, operator review must additionally perform the required
**live smoke** with the pinned binary and dedicated account: verify OAuth refresh
and a harmless model response; inspect the init event for model/version,
`dontAsk`, empty MCP/plugins/skills/slash commands and no unexpected effective
hooks/agents/commands; exercise Read, `cat`, Python, Node and direct
Security.framework/`security` attempts against the canonical plaintext and
namespaced Keychain credential and confirm every attempt is denied; verify
workspace input/output, images, subprocesses, `.venv` Python/pytest, Node and
loopback fixture HTTP still work. Also confirm no managed/policy settings files
exist (the adapter fails closed if known locations are present). These are live
requirements, not claims made by the offline suite.
