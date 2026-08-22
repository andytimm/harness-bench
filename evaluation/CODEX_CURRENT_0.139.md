# Codex CLI 0.139.0 / GPT-5.4 medium evaluation preparation

This evaluation is deliberately isolated under the harness id
`codex-gpt-5.4-medium-current-0.139`. It must not share a result namespace with
older Codex runs.

## Offline preflight

Install/use the project environment, then run either fixed plan without
`--execute`:

```bash
uv run python -m harnessbench.codex_current_runner --plan smoke
uv run python -m harnessbench.codex_current_runner --plan full
```

The runner uses the checked-in `config/harness.example.yaml` by default and prepends
the literal `Path(sys.executable).parent` to `PATH`, ensuring task commands and outcome
oracles use the project environment rather than an unrelated system Python.

Preflight performs no model request and does not copy, refresh, or modify OAuth
state. It checks all 106 tasks, the exact `codex-cli 0.139.0` version, executable
launcher and native-binary path/SHA-256 provenance, the GPT-5.4/medium/provider/billing
pins, a real 0.139 clap argument-parse probe using `--help`, all ordered task/input
hashes, repository cleanliness, and a dedicated Harness-Bench OAuth seed. The
normal `~/.codex/auth.json` path is explicitly rejected.

Current 0.139.0 help was inspected offline for `codex --help`, `codex exec
--help`, and `codex exec resume --help`. The adapter uses supported flags:
`exec`, `exec resume`, `--json`, `--model`, `--config`, `--strict-config`,
`--ignore-user-config`, `--ignore-rules`, and `--output-last-message`.

## Dedicated OAuth provisioning (only after Hermes)

The checked-in namespace requires the regular, non-symlink file
`~/.harnessbench/codex-gpt-5.4-medium-current-0.139/auth.json`. Provision it with
a separate isolated `CODEX_HOME` login after Hermes completes; do **not** copy,
move, symlink, or point the benchmark at `~/.codex/auth.json`. Preflight is
expected to fail with `auth_exists=false` until this deliberate provisioning is
done. The adapter rejects the normal host path even if it contains valid OAuth.
A later operator can provision only that isolated home with:

```bash
install -d -m 700 ~/.harnessbench/codex-gpt-5.4-medium-current-0.139
CODEX_HOME=~/.harnessbench/codex-gpt-5.4-medium-current-0.139 codex login
```

This command is documentation for the post-Hermes operator; it was not run during preparation.

At execution start the runner copies the dedicated seed once into the plan's
private manifest directory. OAuth rotations persist only in that run-private
canonical file across all tasks. The dedicated seed and normal host Codex auth
are never written during tasks. A nonblocking run-directory lock prevents two
runners from sharing the same private canonical file.

## Later integration smokes and full run

The two integration smokes are fixed (not score-selected): `001-file` and
`044-ci-config-repair`.

```bash
uv run python -m harnessbench.codex_current_runner --plan smoke --execute
uv run python -m harnessbench.codex_current_runner --plan smoke --execute --resume
uv run python -m harnessbench.codex_current_runner --plan full --execute --continue-on-failure
uv run python -m harnessbench.codex_current_runner --plan full --execute --resume --continue-on-failure
```

The default manifests are separate: `evaluation/runs/<namespace>/smoke` and
`evaluation/runs/<namespace>/full`, so the commands above work without path
collisions. Use `--manifest-dir` only for an intentionally separate run. A custom path must
be outside the repository or already covered by Git ignore rules. Each manifest
records an attempt as `started` before launch. Terminal successes and failures
are never retried by `--resume`; a non-terminal/interrupted attempt requires
manual investigation rather than an implicit retry. Scores never participate in
retry decisions.

Raw Codex JSONL/stderr/final-message files, native `$CODEX_HOME/sessions`, proxy
records, workspace, adapter state, and executable provenance remain in the task
sandbox. The outer runner log and atomic manifest remain below the manifest
directory.

## Adapter isolation and validation

* Only the required dedicated/run-private `auth.json` is staged (mode 0600) into
task-local `CODEX_HOME`; normal host Codex auth is rejected and never seeded or
written. Source, staging directory, staged file, and lock paths are checked for
regular/non-symlink safety. Writes loop to completion and fsync. Refreshes are
atomically persisted only to the run-private canonical file while its exclusive
lock is held; unexpected source changes cause a refusal rather than an overwrite.
The staged task copy is removed on all exit paths.
* The child environment is built from a small operating-system allowlist. Only
  explicitly approved, non-secret benchmark hook variables may be added; names
  containing token/secret/cookie/credential/password/DSN/auth markers are rejected.
* Model, reasoning, provider, billing mode, CLI version, and native session
  metadata are validated. Missing native reasoning or CLI-version facts fail the
  run. Config overrides, extra args, profiles, feature/network/provider/auth/base
  URL/directory/positional controls, and unknown model-config keys are rejected.
* A POSIX process group is terminated and then killed after the configured grace
  period on timeout.
* Success requires exit zero, a native retained session, `turn.completed`, no
  failure event, strictly JSONL stdout, bounded native call counts, a non-empty
  `--output-last-message` matching the final native agent message, usage, and all
  provenance validations. Multi-round tasks resume the recorded native thread id.
* Codex turn usage retains cached-input tokens while treating them as a subset
  of input (no double-counting). Native token-count events provide model-call
  counts; the turn aggregate supplies exact token totals.

## Resume provenance

The manifest records the exact ordered 106-task list, selected task hashes (including
fixtures/specs/oracles), repository commit and dirty-state digest, complete model config
pins, and resolved launcher/native paths and SHA-256s. Resume compares this structure
strictly. Immediately before every task, the runner re-hashes the task and repository,
re-runs offline Codex preflight, and injects the manifest launcher/native path and digest
as mandatory adapter expectations.

Invalid proxy aggregate `call_count` values remain explicit integrity errors in the result; native-session fallback does not hide them.
