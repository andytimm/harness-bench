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

The plan binds the NFC/resolved seed and config namespace, derived Keychain
service, resolved binary path/version/hash, model/effort, benchmark revision and
every task tree. Changing any value requires a new run root.

Before any tranche, run the dedicated one-shot, non-benchmark live smoke (it
makes no score or benchmark claim and never retries):

```sh
.venv/bin/python evaluation/run_claude_security_smoke.py \
  --run-root /absolute/external/harnessbench-claude-opus46 \
  --benchmark-seed /absolute/dedicated/seed --live \
  --ack I_ACKNOWLEDGE_CLAUDE_SUBSCRIPTION_LIVE_EVALUATION
```

An independent reviewer must inspect its immutable claim, receipt, native/raw/
normalized/stdout/stderr artifacts and hashes. The smoke command deliberately
does **not** approve itself. The reviewer creates a read-only JSON approval with
`schema: 1`, `approved: true`, the exact `plan_binding`, absolute
`smoke_claim_file`/`smoke_receipt_file`, and their SHA256 values. Main tranche
`--live` fails closed without `--smoke-approval /path/to/reviewer-marker.json`.

Live execution also requires a clean planned Git SHA plus `--live` and the
literal acknowledgement. Tranche 1 is the 53 odd task IDs and tranche 2 the 53
even IDs. Claims precede launch. Claims, results and receipts are content-hashed
and bound to the complete plan binding; resume rehashes stdout, stderr, native,
normalized and raw-response artifacts under the expected sandbox and validates
round/session/resume/native-stream correlation.
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
The smoke protocol verifies OAuth/init/policy state, adversarial built-in Read
and Bash `cat`/Python/Node/direct Security.framework/`security` attempts against
plaintext authentication and the exact namespaced Keychain service, and positive
workspace/image/subprocess/resolved `.venv` Python/Node/loopback capabilities.
It stops unconditionally after one Claude invocation. Managed JSON, plist and
`managed-settings.d` sources (including the per-user Managed Preferences plist)
are enumerated and rejected before launch. Offline tests do not make these live
claims and never access authentication.
