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
the entire run. The parent Claude/auth process retains the real login `HOME`:
macOS Security.framework default-Keychain discovery fails under a synthetic HOME,
and Claude 2.1.227 also consults normal non-secret `.claude.json` account-selection
metadata. `HOME` is stripped from native Bash/descendants, while compact exact high-value
denies block all task tools from normal `.claude`, `.claude.json`, Keychains,
credential directories, and benchmark control planes (with explicit workspace/runtime
capabilities). A symlink-safe namespace lock serializes status-only exact-service
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
missing init evidence, or a changed tool list fail closed. Claude 2.1.227's exact
inert built-in agent-name list (`claude`, `Explore`, `general-purpose`, `Plan`) is
accepted only while neither `Agent` nor `Task` is exposed; any other agent list
fails closed.

Sensitive paths are enumerated at launch. The production native policy uses a
compact explicit high-value frontier: the current checkout's `.git`, `tasks`,
`grading`, `evaluation`, `src`, and `config` integrity roots, other worktrees, dedicated `.harnessbench`, normal `.claude` and
`.claude.json`, `Library/Keychains`, `.ssh`, `.aws`, `.config`, `.codex`, exact Hermes auth/config/`.env`, `.prime`, Security.framework,
`/usr/bin/security`, and private smoke evidence. No native deny overlaps any
workspace/runtime allow, preventing Claude from expanding a broad HOME parent
into a profile above macOS `ARG_MAX`. This is a calibrated trust boundary for
authentication and benchmark integrity, not a claim that Bash or built-in tools
are denied every benign file in the user's home. Built-ins use the same compact
high-value roots and, like native Bash, list only those six current-checkout
integrity roots; a checkout-root deny would override the merged `.venv` allow.
They do not enumerate HOME or benign checkout files/docs/tests. Native credential-file entries remain a small exact set and
never deny a workspace ancestor. Because Claude merges permission paths into its
Bash profile, the fail-closed shape check prefix-minimizes the union of filesystem,
credential, and built-in paths, prohibits a broad HOME prefix or any merged allow/deny ancestry, caps the union at
30, and budgets 32 KB per prefix / 960 KB total from the observed 92-prefix to
325-generated-path, 1.9-MB smoke. A production-shaped test covers the combined
union and native deny/allow ancestry. The offline Seatbelt probe preserves the
same effective frontier.
Native read capabilities include the workspace, literal `.venv`, resolved uv
Python runtime, pinned binary, Node, and explicit task capabilities; the probe
executes literal `ROOT/.venv/bin/python -m pytest --version`.

Run the production-equivalent offline native probe before review (no auth
mutation, model call, or network service other than its loopback fixture):

```sh
.venv/bin/python evaluation/run_claude_native_sandbox_probe.py \
  --benchmark-seed /absolute/dedicated/seed
```

It creates one Seatbelt sandbox (never a nested sandbox), executes the exact
immutable generated Bash program through all negative and positive probes, and
accepts only its sole JSON report.  It uses the exact real Keychain service but
never requests or prints the secret. Parent exact-service availability is checked
status-only before and after; no credential file is expected or accessed. Its
cat/Python/Node probes target a harmless synthetic private-evidence file outside
the allowed workspace and fail without emitting its contents. The live security smoke uses a harmless `sandbox/private-evidence` sentinel
outside the allowed workspace, created without following links and hash-checked.
Read/Edit/Write target that existing file while Glob/Grep target sensitive
directories. Validation requires explicit policy-denial text rather than incidental
file-shape/tool errors; Claude's Edit-only unread prerequisite is accepted because
2.1.227 checks it before permissions, but only with the exact existing-file call,
structurally validated Edit deny, and unchanged sentinel hash. It separately proves
blocking for every file-capable built-in and
arbitrary Bash cat/Python/Node/`security`/Security.framework access, plus
workspace, image, subprocess, literal venv Python, pytest, Node, and loopback
capabilities.  Its immutable receipt carries a native-security marker required
by both tranche gates.

Claude Code 2.1.227 has a known loopback routing defect: its injected
`NO_PROXY` includes `localhost`/`127.0.0.1`, so clients bypass the sandbox proxy
and native Seatbelt denies the direct connection even when
`sandbox.network.allowedDomains` permits loopback. See upstream reports
[anthropics/claude-code#65482](https://github.com/anthropics/claude-code/issues/65482)
and [#28018](https://github.com/anthropics/claude-code/issues/28018). The smoke
therefore prefixes its one exact Bash command with `NO_PROXY= no_proxy=`. For
benchmark tasks, the adapter appends the same narrowly scoped operational
instruction only when a hook supplies a `MOCK_*` URL whose host is exactly
`127.0.0.1` or `localhost`; metadata records original/effective prompt hashes
and the instruction provenance. Parent-owned fixtures do not require
`network.allowLocalBinding`, which remains disabled.

The unavoidable supported-auth tradeoff is that the unsandboxed parent Claude
process can read and may update normal `.claude.json` account-selection metadata;
the adapter does not claim that file is immutable. Task tools cannot access it.
OAuth remains only in the custom namespaced Keychain service, and harness status
checks name only that exact custom service, never the default service.

Managed JSON, plist, and `managed-settings.d` sources (including per-user
Managed Preferences) are enumerated and rejected before launch.  Offline tests
make no live claim and never access or mutate authentication.  A live launch
still remains unresolved until an independently reviewed, passing one-shot
security smoke produces the plan-bound approval marker described above.
