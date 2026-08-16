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

`run_claude_full.py` pins Claude Code `2.1.227 (Claude Code)` and the resolved
binary SHA256 `7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7`,
model `claude-opus-4-6`, and effort `medium`. It never reads `~/.claude`.
Before evaluation, an operator must independently provision subscription OAuth
in the dedicated seed directory `~/.harnessbench/claude-code-opus-4.6`; this
repository does not authenticate or copy normal Claude credentials.

First inspect the immutable 106-task plan without authentication or model calls:

```sh
.venv/bin/python evaluation/run_claude_full.py \
  --run-root /absolute/external/harnessbench-claude-opus46 \
  --tranche 1 --dry-plan
```

Live execution is deliberately gated by `--live` plus the literal acknowledgement
printed by `--help`. Run tranche 1 (odd numeric task IDs) and then tranche 2
(even IDs). Each has 53 tasks from the same immutable plan. Claims are written
before launch and receipts after strict validation; an interrupted claimed task
is never retried automatically, and a rejected rate-limit event is recorded as
`quota_censored` and stops scheduling. Process grading is off and
`HARNESSBENCH_PUBLIC_URL_TEMPLATE` is exactly `{local_url}`.

Containment is two-layered and fail closed: macOS Seatbelt denies the benchmark
checkout/control plane, auth seed, and oracle data while preserving `.venv`, the
workspace, shell/subprocess, Node, images, and loopback; Claude's explicit
`--settings` disables hooks/plugins/MCP/skills/memory/customization and enables
its native Bash sandbox with credential/read denies (including Keychain CLI).
The built-in Read tool receives matching deny rules. A live launch still requires
an operator security smoke proving OAuth succeeds while Read, cat, Python, Node,
and `/usr/bin/security` cannot recover the staged credential.
