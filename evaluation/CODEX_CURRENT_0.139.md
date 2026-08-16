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

Preflight performs no model request and does not copy, refresh, or modify OAuth
state. It checks all 106 tasks, the exact `codex-cli 0.139.0` version, executable
launcher and native-binary path/SHA-256 provenance, the GPT-5.4/medium/provider/billing
pins, a real 0.139 clap argument-parse probe using `--help`, all ordered task/input
hashes, repository cleanliness, and only a non-secret classification of
`~/.codex/auth.json`.

Current 0.139.0 help was inspected offline for `codex --help`, `codex exec
--help`, and `codex exec resume --help`. The adapter uses supported flags:
`exec`, `exec resume`, `--json`, `--model`, `--config`, `--strict-config`,
`--ignore-user-config`, `--ignore-rules`, and `--output-last-message`.

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
collisions. Use `--manifest-dir` only for an intentionally separate run. Each manifest
records an attempt as `started` before launch. Terminal successes and failures
are never retried by `--resume`; a non-terminal/interrupted attempt requires
manual investigation rather than an implicit retry. Scores never participate in
retry decisions.

Raw Codex JSONL/stderr/final-message files, native `$CODEX_HOME/sessions`, proxy
records, workspace, adapter state, and executable provenance remain in the task
sandbox. The outer runner log and atomic manifest remain below the manifest
directory.

## Adapter isolation and validation

* Only `auth.json` is copied (mode 0600) into task-local `CODEX_HOME`; user
  config/rules are ignored. The staged file is removed on success, timeout,
  spawn error, pre-spawn error, and post-processing exceptions. Auth source and
  staging paths must be non-symlink regular files. Refreshed subscription OAuth is
  copied back only with a digest compare-and-swap under a lock; a concurrent host
  refresh is never overwritten.
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
