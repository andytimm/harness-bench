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

Provision a dedicated mode-0700 namespace (never `~/.claude`) and log in so
Claude 2.1.227 stores subscription OAuth only in its macOS Keychain service,
`Claude Code-credentials-<first 8 hex of SHA256(NFC(absolute namespace path))>`.
The directory contains no plaintext authentication file or customization. It is
the exact stable `CLAUDE_CONFIG_DIR` and `CLAUDE_SECURESTORAGE_CONFIG_DIR` for
the entire run. A symlink-safe namespace lock serializes status-only exact-service
Security.framework checks, pinned `claude auth status --json`, and each complete
invocation. Only status codes and authorization booleans are retained; normal
`~/.claude` authentication is neither used nor changed.

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

## Reviewed Claude containment contract

Claude 2.1.227 is **not** launched under an outer `sandbox-exec`: macOS
Seatbelt sandboxes cannot be safely nested with Claude's native Bash sandbox.
Claude's exact native sandbox (`enabled: true`, `failIfUnavailable: true`,
`allowUnsandboxedCommands: false`) is the sole OS boundary for Bash and every
Bash descendant.  The parent Claude process remains outside that tool sandbox
only so it can access the dedicated namespaced OAuth item.

This is an explicit two-control threat model, not an outer-OS-sandbox claim.
Bash/descendants are controlled by the native macOS sandbox.  The in-process
Read, Edit, Write, Glob, and Grep tools are controlled by explicit built-in
permission denies; they are not claimed to have a separate OS boundary.  The
only exposed tools are exactly those five plus Bash.  `--safe-mode`, empty
setting sources, strict empty MCP, disabled skills/slash commands, and the exact
init tool list are runtime-validated.  Invalid or silently ignored settings,
missing init evidence, or a changed tool list fail closed.

Sensitive paths are enumerated at launch: the host HOME deny frontier except
explicit capability roots, every checkout/control-plane entry except the
read-only visible `.venv`, every other Git worktree, normal and benchmark auth,
prior run claims/results visible on the host, and oracle/grading roots.  Native
read capabilities include both the literal `.venv` and the resolved uv Python
runtime; the probe executes literal `ROOT/.venv/bin/python -m pytest --version`.

Run the production-equivalent offline native probe before review (no auth
mutation, model call, or network service other than its loopback fixture):

```sh
.venv/bin/python evaluation/run_claude_native_sandbox_probe.py \
  --benchmark-seed /absolute/dedicated/seed
```

It creates one Seatbelt sandbox (never a nested sandbox), executes the exact
immutable generated Bash program through all negative and positive probes, and
accepts only its sole JSON report.  It uses the exact real Keychain service but
never requests or prints the secret.  Parent lookup equality is checked only by
status, and the credential file by SHA-256 before/after.  The live security
smoke separately proves explicit denial for every file-capable built-in and
arbitrary Bash cat/Python/Node/`security`/Security.framework access, plus
workspace, image, subprocess, literal venv Python, pytest, Node, and loopback
capabilities.  Its immutable receipt carries a native-security marker required
by both tranche gates.

Managed JSON, plist, and `managed-settings.d` sources (including per-user
Managed Preferences) are enumerated and rejected before launch.  Offline tests
make no live claim and never access or mutate authentication.  A live launch
still remains unresolved until an independently reviewed, passing one-shot
security smoke produces the plan-bound approval marker described above.
