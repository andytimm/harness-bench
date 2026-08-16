from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult

EXPECTED_VERSION = "2.1.227 (Claude Code)"
EXPECTED_SHA256 = "7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7"
EXPECTED_MODEL = "claude-opus-4-6"
EXPECTED_EFFORT = "medium"
_CREDENTIAL = ".credentials.json"
_SECRET_SUFFIXES = ("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _path(value: str | Path) -> Path:
    result = Path(os.path.expanduser(str(value)))
    return result if result.is_absolute() else (_project_root() / result)


def _resolve_binary(command: str) -> Path:
    found = shutil.which(command) if not os.path.isabs(command) else command
    if not found:
        raise ValueError(f"Claude Code command is unavailable: {command}")
    path = Path(found).resolve()
    if not path.is_file():
        raise ValueError(f"Claude Code command is not a file: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version(binary: Path) -> str:
    try:
        result = subprocess.run([str(binary), "--version"], text=True, capture_output=True,
                                timeout=10, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout.strip() or result.stderr.strip()) if result.returncode == 0 else ""


def _validate_seed(seed: Path) -> Path:
    normal = (Path.home() / ".claude").resolve()
    resolved_seed = seed.resolve()
    if resolved_seed == normal or normal in resolved_seed.parents:
        raise ValueError("benchmark_config_seed must not use normal ~/.claude authentication")
    if seed.is_symlink() or not seed.is_dir():
        raise ValueError(f"dedicated benchmark Claude config seed is unavailable: {seed}")
    credential = seed / _CREDENTIAL
    if credential.is_symlink() or not credential.is_file():
        raise ValueError(f"dedicated benchmark OAuth credential is unavailable or symlinked: {credential}")
    try:
        value = json.loads(credential.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid benchmark OAuth credential: {exc}") from exc
    oauth = value.get("claudeAiOauth") if isinstance(value, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken") or not oauth.get("refreshToken"):
        raise ValueError("benchmark subscription OAuth credential is incomplete")
    return credential


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush(); os.fsync(dst.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _sync_refresh(staged: Path, seed: Path) -> bool:
    if staged.is_symlink() or not staged.is_file():
        return False
    try:
        refreshed = json.loads(staged.read_text(encoding="utf-8"))
        oauth = refreshed.get("claudeAiOauth") if isinstance(refreshed, dict) else None
        if not isinstance(oauth, dict) or not oauth.get("accessToken") or not oauth.get("refreshToken"):
            return False
        lock = seed.with_suffix(seed.suffix + ".lock")
        with lock.open("a+") as handle:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _atomic_copy(staged, seed)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _unlink(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink(): path.unlink()
    except OSError:
        pass


def _clean_env(overrides: dict[str, str], config_dir: Path, ctx: AdapterRunContext) -> dict[str, str]:
    keep = {"PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "SHELL", "TERM", "USER", "LOGNAME", "SSH_TTY"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    # Hook-provided values are task capabilities, not host credentials.
    env.update(overrides)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(_SECRET_SUFFIXES) or upper.startswith(("AWS_SECRET_", "AZURE_CLIENT_SECRET")):
            env.pop(key, None)
    env.update({
        "HOME": str(ctx.sandbox), "CLAUDE_CONFIG_DIR": str(config_dir), "NO_COLOR": "1",
        "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "0",
        "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1", "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1",
        "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "1", "CLAUDE_CODE_DISABLE_CLAUDE_CODE_SKILL": "1",
        "CLAUDE_CODE_AUTO_CONNECT_IDE": "0", "CLAUDE_CODE_SKIP_PLUGIN_MCP_SERVERS": "1",
        "CLAUDE_CODE_SYNC_PLUGINS": "0", "CLAUDE_CODE_SYNC_SKILLS": "0",
        "WORKSPACE": str(ctx.workspace), "HARNESSBENCH_WORKSPACE": str(ctx.workspace),
        "HARNESSBENCH_SANDBOX": str(ctx.sandbox), "HARNESSBENCH_TASK_ID": ctx.task.task_id,
        "HARNESSBENCH_SESSION_ID": ctx.session_id, "HARNESSBENCH_MODEL_ID": ctx.model_id,
    })
    return env


def _rows(text: str) -> list[dict[str, Any]]:
    result = []
    for line in text.splitlines():
        try: item = json.loads(line)
        except json.JSONDecodeError: continue
        if isinstance(item, dict): result.append(item)
    return result


def _text(content: Any) -> str:
    if isinstance(content, str): return content
    if not isinstance(content, list): return ""
    return "\n".join(str(block.get("text")) for block in content
                      if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))


def _write_trace(rows: list[dict[str, Any]], proxy_dir: Path, ctx: AdapterRunContext,
                 stdout_log: Path, native_session: str, round_number: int) -> dict[str, Any]:
    responses = proxy_dir / "responses"; responses.mkdir(parents=True, exist_ok=True)
    assistants = [row for row in rows if row.get("type") == "assistant" and isinstance(row.get("message"), dict)]
    result_rows = [row for row in rows if row.get("type") == "result"]
    terminal = result_rows[-1] if result_rows else {}
    for index, row in enumerate(assistants, 1):
        message = row["message"]
        path = responses / f"claude-round{round_number:02d}-{index:04d}.json"
        path.write_text(json.dumps({
            "task_id": ctx.task.task_id, "session_id": ctx.session_id,
            "native_session_id": native_session, "model_id": ctx.model_id,
            "framework": "claude_code", "provider": "anthropic-subscription-oauth",
            "request_body": None,
            "response_json": {"model": EXPECTED_MODEL, "choices": [{"message": {"role": "assistant", "content": _text(message.get("content"))}}]},
            "source_stdout_log_file": str(stdout_log), "source_event_index": index,
        }, indent=2), encoding="utf-8")
    # Claude's final result.modelUsage is the sole authoritative, cumulative
    # source. Never sum assistant-message usage or prior resumed result rows.
    model_usage = terminal.get("modelUsage") if isinstance(terminal.get("modelUsage"), dict) else {}
    selected = model_usage.get(EXPECTED_MODEL) if set(model_usage) == {EXPECTED_MODEL} else None
    call_count = terminal.get("num_turns", len(assistants))
    if terminal and assistants and isinstance(selected, dict) and isinstance(call_count, int) and call_count > 0:
        input_tokens = int(selected.get("inputTokens", 0) or 0)
        output_tokens = int(selected.get("outputTokens", 0) or 0)
        cache_read = int(selected.get("cacheReadInputTokens", 0) or 0)
        cache_write = int(selected.get("cacheCreationInputTokens", 0) or 0)
        log = proxy_dir / "requests.jsonl"
        retained = []
        if log.is_file():
            retained = [line for line in log.read_text(encoding="utf-8").splitlines()
                        if '"usage_source": "claude_result_modelUsage"' not in line]
        retained.append(json.dumps({
            "task_id": ctx.task.task_id, "session_id": ctx.session_id,
            "provider": "anthropic-subscription-oauth", "response_model": EXPECTED_MODEL,
            "call_count": call_count, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
            "total_tokens": input_tokens + output_tokens,
            "usage_source": "claude_result_modelUsage", "native_session_id": native_session,
        }))
        log.write_text("\n".join(retained) + "\n", encoding="utf-8")
    return {"response_count": len(assistants), "result_count": len(result_rows), "usage_source": "claude_result_modelUsage"}


def _terminate(proc: subprocess.Popen[str], grace: float) -> int:
    if proc.poll() is not None: return int(proc.returncode or 0)
    try:
        os.killpg(proc.pid, signal.SIGTERM) if os.name == "posix" else proc.terminate()
        return proc.wait(timeout=max(.1, grace))
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try: os.killpg(proc.pid, signal.SIGKILL) if os.name == "posix" else proc.kill()
        except ProcessLookupError: pass
        return proc.wait()


class ClaudeCodeAdapter(BaseAdapter):
    name = "claude_code"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        cfg = ctx.model_config
        model = str(cfg.get("model") or "")
        effort = str(cfg.get("effort") or "")
        expected_version = str(cfg.get("expected_version") or "")
        expected_hash = str(cfg.get("expected_sha256") or "")
        if (model, effort, expected_version, expected_hash) != (EXPECTED_MODEL, EXPECTED_EFFORT, EXPECTED_VERSION, EXPECTED_SHA256):
            return AdapterRunResult(ok=False, stderr="Claude Code model/effort/version/hash pins must exactly match the approved evaluation")
        try:
            binary = _resolve_binary(str(cfg.get("command") or "claude"))
            actual_hash = _sha256(binary)
            actual_version = _version(binary)
            if actual_hash != EXPECTED_SHA256: raise ValueError(f"Claude Code binary SHA256 mismatch: {actual_hash}")
            if actual_version != EXPECTED_VERSION: raise ValueError(f"Claude Code version mismatch: {actual_version!r}")
            seed_dir = _path(str(cfg.get("benchmark_config_seed") or ""))
            seed_credential = _validate_seed(seed_dir)
        except ValueError as exc:
            return AdapterRunResult(ok=False, stderr=str(exc))

        config_dir = ctx.sandbox / ".claude-benchmark"
        if config_dir.is_symlink():
            return AdapterRunResult(ok=False, stderr="isolated Claude config directory must not be a symlink")
        config_dir.mkdir(parents=True, exist_ok=True)
        staged = config_dir / _CREDENTIAL
        _atomic_copy(seed_credential, staged)
        settings = config_dir / "benchmark-settings.json"
        settings_payload = {
            "hooks": {}, "enabledPlugins": {}, "extraKnownMarketplaces": {},
            "permissions": {
                "allow": ["Read", f"Write({ctx.workspace}/**)", f"Edit({ctx.workspace}/**)", "Glob", "Grep", "Bash"],
                "deny": [f"Read({staged})", f"Read({staged}/**)", f"Read({seed_dir}/**)",
                         f"Read({_project_root()}/**)", f"Write({_project_root()}/**)",
                         f"Read({Path.home()}/.claude/**)"],
                "additionalDirectories": [],
            },
            "sandbox": {
                "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False, "excludedCommands": [],
                "credentials": {
                    "files": [{"path": str(staged), "mode": "deny"},
                              {"path": str(seed_dir), "mode": "deny"},
                              {"path": str(Path.home() / ".claude"), "mode": "deny"}],
                    "envVars": [],
                },
                "filesystem": {
                    "allowWrite": [str(ctx.workspace)],
                    "denyRead": [str(staged), str(seed_dir), str(_project_root()), str(Path.home() / ".claude"), "/usr/bin/security"],
                    "denyWrite": [str(seed_dir), str(_project_root()), str(Path.home() / ".claude")],
                },
            },
        }
        settings.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")
        mcp = config_dir / "empty-mcp.json"; mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        state_file = config_dir / "adapter-state.json"
        try: state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        except (OSError, json.JSONDecodeError): state = {}
        round_number = int(state.get("rounds", 0) or 0) + 1
        prior = str(state.get("session_id") or "")
        native_session = prior or str(uuid.uuid4())
        try: uuid.UUID(native_session)
        except ValueError:
            _unlink(staged); return AdapterRunResult(ok=False, stderr="invalid persisted Claude native session UUID")

        reserved = {"--model", "--effort", "--resume", "--session-id", "--output-format", "--input-format",
                    "--settings", "--setting-sources", "--mcp-config", "--strict-mcp-config", "--bare",
                    "--fallback-model", "--permission-mode", "--dangerously-skip-permissions", "--tools",
                    "--allowedTools", "--disallowedTools", "--system-prompt", "--append-system-prompt"}
        extras = [str(v) for v in cfg.get("extra_args", [])]
        if any(v in reserved or any(v.startswith(x + "=") for x in reserved) for v in extras):
            _unlink(staged); return AdapterRunResult(ok=False, stderr="reserved Claude Code extra_args are not allowed")
        cmd = [str(binary), "-p", "--verbose", "--output-format", "stream-json", "--model", model,
               "--effort", effort, "--permission-mode", "dontAsk",
               "--disable-slash-commands", "--setting-sources", "", "--settings", str(settings),
               "--strict-mcp-config", "--mcp-config", str(mcp)]
        if prior: cmd += ["--resume", native_session]
        else: cmd += ["--session-id", native_session]
        cmd += extras + [ctx.prompt]
        env = _clean_env(ctx.env, config_dir, ctx)
        execution_cmd = cmd
        containment_contract = ""
        if bool(cfg.get("require_macos_containment", False)):
            try:
                from harnessbench.macos_containment import containment_paths, seatbelt_profile, CONTAINMENT_CONTRACT
                sandbox_exec = Path("/usr/bin/sandbox-exec")
                if sys.platform != "darwin" or not sandbox_exec.is_file():
                    raise RuntimeError("reviewed macOS sandbox-exec containment is unavailable")
                read_write, write_only = containment_paths(_project_root(), seed_dir)
                profile = seatbelt_profile(read_write, write_only)
                execution_cmd = [str(sandbox_exec), "-p", profile, *cmd]
                containment_contract = CONTAINMENT_CONTRACT
            except RuntimeError as exc:
                _unlink(staged); return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))
        stdout_log = ctx.sandbox / f"claude-round{round_number}.stdout.jsonl"
        stderr_log = ctx.sandbox / f"claude-round{round_number}.stderr.log"
        timed_out = False
        try:
            proc = subprocess.Popen(execution_cmd, cwd=ctx.workspace, env=env, text=True, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=(os.name == "posix"))
            try: stdout, stderr = proc.communicate(timeout=ctx.timeout_sec); returncode = int(proc.returncode or 0)
            except subprocess.TimeoutExpired:
                timed_out = True; returncode = _terminate(proc, float(cfg.get("timeout_grace_sec", 5) or 5)); stdout, stderr = proc.communicate()
        except OSError as exc:
            _unlink(staged); return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))
        except BaseException:
            if "proc" in locals() and proc.poll() is None:
                try: _terminate(proc, 1)
                except Exception: pass
            _unlink(staged)
            raise
        # Refresh is copied back under a lock, then the staged secret is removed
        # before any fallible parsing/trace persistence. No symlink is ever used.
        synced = _sync_refresh(staged, seed_credential) if bool(cfg.get("sync_refreshed_auth", True)) else False
        _unlink(staged)
        stdout_log.write_text(stdout, encoding="utf-8"); stderr_log.write_text(stderr, encoding="utf-8")
        rows = _rows(stdout)
        init = next((r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"), {})
        results = [r for r in rows if r.get("type") == "result"]
        terminal = results[-1] if results else {}
        ids = {str(r.get("session_id")) for r in rows if r.get("session_id")}
        session_ok = ids == {native_session}
        disabled_fields_ok = all(init.get(key) == [] for key in ("mcp_servers", "plugins", "skills", "slash_commands"))
        init_ok = (init.get("model") == model and init.get("claude_code_version") == "2.1.227"
                   and init.get("session_id") == native_session and init.get("permissionMode") == "dontAsk"
                   and disabled_fields_ok)
        model_usage = terminal.get("modelUsage") if isinstance(terminal.get("modelUsage"), dict) else {}
        terminal_ok = (terminal.get("subtype") == "success" and terminal.get("is_error") is False and
                       terminal.get("session_id") == native_session and set(model_usage) == {model}
                       and isinstance(model_usage.get(model), dict))
        rate_limit_rows = [r for r in rows if r.get("type") == "rate_limit_event"]
        quota_censored = any(r.get("subtype") == "rejected" or
                             (isinstance(r.get("rate_limit_info"), dict) and r["rate_limit_info"].get("status") == "rejected")
                             for r in rate_limit_rows)
        trace = _write_trace(rows, ctx.sandbox / "usage-proxy", ctx, stdout_log, native_session, round_number)
        native_candidates = list(config_dir.rglob(f"{native_session}.jsonl"))
        native_trace_ok = len(native_candidates) == 1
        ok = (returncode == 0 and not timed_out and not quota_censored and session_ok and init_ok and terminal_ok
              and native_trace_ok and trace["response_count"] > 0 and trace["result_count"] == 1)
        state_file.write_text(json.dumps({"rounds": round_number, "session_id": native_session}, indent=2), encoding="utf-8")
        return AdapterRunResult(ok=ok, command=cmd, stdout=stdout, stderr=stderr, metadata={
            "returncode": returncode, "timed_out": timed_out, "claude_version": actual_version,
            "binary": str(binary), "binary_sha256": actual_hash, "model": model, "effort": effort,
            "native_session_id": native_session, "round_number": round_number, "resumed": bool(prior),
            "session_ids_valid": session_ok, "init_valid": init_ok, "terminal_valid": terminal_ok,
            "disabled_features_valid": disabled_fields_ok, "quota_censored": quota_censored,
            "rate_limit_event_count": len(rate_limit_rows),
            "native_session_file": str(native_candidates[0]) if native_trace_ok else "",
            "stdout_log_file": str(stdout_log), "stderr_log_file": str(stderr_log),
            "native_trace_dir": str(config_dir), "synthetic_trace": trace,
            "staged_credential_removed": not staged.exists(), "refreshed_auth_synced": synced,
            "benchmark_config_seed": str(seed_dir), "settings_sources": [], "mcp_disabled": True,
            "containment_contract": containment_contract, "execution_command": execution_cmd,
        })
