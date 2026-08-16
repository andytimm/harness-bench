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
import stat
import unicodedata
import getpass
from contextlib import contextmanager
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


def _keychain_service(config_dir: Path) -> str:
    """Claude 2.1.227 non-default macOS OAuth service namespace."""
    canonical = unicodedata.normalize("NFC", str(config_dir.resolve()))
    return "Claude Code-credentials-" + hashlib.sha256(canonical.encode()).hexdigest()[:8]


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


def _assert_secure_path(path: Path, *, directory: bool) -> None:
    """Reject symlinks in the dedicated namespace and group/world-accessible secrets."""
    absolute = path.absolute()
    cursor = absolute
    while True:
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise ValueError(f"dedicated Claude path is unavailable: {cursor}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) and cursor != Path("/var"):
            raise ValueError(f"dedicated Claude path has a symlink ancestor: {cursor}")
        if cursor == Path.home() or cursor.parent == cursor:
            break
        cursor = cursor.parent
    info = absolute.stat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"dedicated Claude config namespace is not a directory: {absolute}")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError(f"dedicated Claude credential is not a regular file: {absolute}")
    maximum = 0o700 if directory else 0o600
    if stat.S_IMODE(info.st_mode) & ~maximum:
        raise ValueError(f"dedicated Claude path permissions are too broad: {absolute}")


def _validate_seed(seed: Path) -> Path:
    normal = (Path.home() / ".claude").resolve()
    resolved_seed = seed.resolve()
    if resolved_seed == normal or normal in resolved_seed.parents:
        raise ValueError("benchmark_config_seed must not use normal ~/.claude authentication")
    _assert_secure_path(seed, directory=True)
    forbidden = [seed / name for name in ("settings.json", "settings.local.json", "CLAUDE.md",
                 "commands", "agents", "plugins", "skills", "hooks")]
    present = [str(path) for path in forbidden if path.exists() or path.is_symlink()]
    if present:
        raise ValueError("dedicated Claude namespace contains customization: " + ", ".join(present))
    credential = seed / _CREDENTIAL
    _assert_secure_path(credential, directory=False)
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


def _open_lock(path: Path) -> int:
    """Open a private, non-symlink, single-link regular lock file."""
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise ValueError(f"unsafe Claude namespace lock symlink: {path}")
    except FileNotFoundError:
        pass
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"unsafe Claude namespace lock: {path}")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _sync_refresh(staged: Path, seed: Path) -> bool:
    if staged.is_symlink() or not staged.is_file():
        return False
    try:
        refreshed = json.loads(staged.read_text(encoding="utf-8"))
        oauth = refreshed.get("claudeAiOauth") if isinstance(refreshed, dict) else None
        if not isinstance(oauth, dict) or not oauth.get("accessToken") or not oauth.get("refreshToken"):
            return False
        lock = seed.with_suffix(seed.suffix + ".lock")
        fd = _open_lock(lock)
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            _atomic_copy(staged, seed)
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _unlink(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink(): path.unlink()
    except OSError:
        pass


@contextmanager
def _namespace_lock(config_dir: Path):
    """Serialize use of the one canonical OAuth namespace (including refresh)."""
    lock = config_dir / ".harnessbench.lock"
    fd = _open_lock(lock)
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.write("\n"); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _parse_stream(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed stream-json line {number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("type"), str):
            raise ValueError(f"invalid stream-json event at line {number}")
        rows.append(row)
    if not rows:
        raise ValueError("empty stream-json transcript")
    results = [i for i, row in enumerate(rows) if row.get("type") == "result"]
    if len(results) != 1 or results[0] != len(rows) - 1:
        raise ValueError("stream-json must have exactly one terminal result event")
    inits = [i for i, row in enumerate(rows) if row.get("type") == "system" and row.get("subtype") == "init"]
    if len(inits) != 1 or inits[0] != 0:
        raise ValueError("stream-json must begin with exactly one init event")
    event_ids = [str(r["uuid"]) for r in rows if r.get("uuid")]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate stream-json event UUID")
    return rows


def _parse_native_transcript(path: Path, native_session: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed native transcript line {number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object native transcript line {number}")
        rows.append(row)
    if not rows: raise ValueError("empty native transcript")
    ids = {str(row.get("sessionId") or row.get("session_id")) for row in rows
           if row.get("sessionId") or row.get("session_id")}
    if ids and ids != {native_session}: raise ValueError("native transcript session correlation failed")
    uuids = [str(row["uuid"]) for row in rows if row.get("uuid")]
    if len(uuids) != len(set(uuids)): raise ValueError("duplicate native transcript UUID")
    return rows


def _normalize_trace(rows: list[dict[str, Any]], path: Path, *, native_session: str,
                     round_number: int, expected_model: str) -> str:
    events = []
    tool_ids: set[str] = set()
    completed: set[str] = set()
    actual_models: set[str] = set()
    for index, row in enumerate(rows):
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if isinstance(message.get("model"), str): actual_models.add(message["model"])
        content = message.get("content") if isinstance(message.get("content"), list) else []
        blocks = []
        for block in content:
            if not isinstance(block, dict):
                blocks.append({"type": "unknown", "raw": block}); continue
            kind = block.get("type")
            if kind == "tool_use" and isinstance(block.get("id"), str):
                if block["id"] in tool_ids: raise ValueError("duplicate tool_use id")
                tool_ids.add(block["id"])
            if kind == "tool_result" and isinstance(block.get("tool_use_id"), str):
                tid = block["tool_use_id"]
                if tid not in tool_ids or tid in completed: raise ValueError("orphan or duplicate tool_result")
                completed.add(tid)
            blocks.append(block)  # lossless JSON value, including thinking/signatures/errors
        events.append({"index": index, "type": row.get("type"), "subtype": row.get("subtype"),
                       "uuid": row.get("uuid"), "session_id": row.get("session_id"),
                       "parent_tool_use_id": row.get("parent_tool_use_id"), "content": blocks,
                       "raw": row})
    if not rows:
        raise ValueError("empty normalized trace")
    terminal = rows[-1]
    usage = terminal.get("modelUsage") if isinstance(terminal.get("modelUsage"), dict) else {}
    actual_models.update(str(x) for x in usage)
    if actual_models != {expected_model}:
        raise ValueError(f"unexpected actual model set: {sorted(actual_models)}")
    payload = {"schema": 1, "native_session_id": native_session, "round": round_number,
               "actual_model": expected_model, "events": events}
    _atomic_json(path, payload)
    return _sha256(path)


def _managed_policy_candidates(user: str | None = None) -> list[Path]:
    """Every enterprise/policy source consulted by pinned Claude Code 2.1.227."""
    user = user or getpass.getuser()
    library = Path("/Library")
    bases = [
        library / "Application Support" / "ClaudeCode" / "managed-settings.json",
        library / "Application Support" / "ClaudeCode" / "managed-mcp.json",
        library / "Application Support" / "ClaudeCode" / "managed-settings.d",
        library / "Managed Preferences" / "com.anthropic.claudecode.plist",
        library / "Managed Preferences" / user / "com.anthropic.claudecode.plist",
        library / "Preferences" / "com.anthropic.claudecode.plist",
        Path("/etc/claude-code/managed-settings.json"),
        Path("/etc/claude-code/managed-mcp.json"),
        Path("/etc/claude-code/managed-settings.d"),
    ]
    # Reject both a policy directory itself and any entry, including dangling
    # symlinks.  The directory check makes this fail closed for new drop-ins.
    return bases


def _unexpected_managed_policy(user: str | None = None) -> list[str]:
    candidates = _managed_policy_candidates(user)
    found: list[str] = []
    for path in candidates:
        if path.exists() or path.is_symlink():
            found.append(str(path))
        if path.name == "managed-settings.d" and path.is_dir():
            found.extend(str(item) for item in sorted(path.iterdir(), key=lambda p: p.name))
    return sorted(set(found))


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
        "HOME": str(ctx.sandbox), "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(config_dir), "NO_COLOR": "1",
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


def _quota_rejected(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        if row.get("type") != "rate_limit_event": continue
        info = row.get("rate_limit_info") if isinstance(row.get("rate_limit_info"), dict) else {}
        if row.get("subtype") in {"rejected", "rate_limit_rejected"} or info.get("status") in {"rejected", "blocked"}:
            return True
    return False


def _write_trace(rows: list[dict[str, Any]], proxy_dir: Path, ctx: AdapterRunContext,
                 stdout_log: Path, native_session: str, round_number: int) -> dict[str, Any]:
    responses = proxy_dir / "responses"; responses.mkdir(parents=True, exist_ok=True)
    assistants = [row for row in rows if row.get("type") == "assistant" and isinstance(row.get("message"), dict)]
    result_rows = [row for row in rows if row.get("type") == "result"]
    terminal = result_rows[-1] if result_rows else {}
    raw_artifacts: list[dict[str, str]] = []
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
        raw_artifacts.append({"path": str(path), "sha256": _sha256(path)})
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
    return {"response_count": len(assistants), "result_count": len(result_rows),
            "usage_source": "claude_result_modelUsage", "raw_response_artifacts": raw_artifacts}


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
        managed_policy = _unexpected_managed_policy()
        if managed_policy:
            return AdapterRunResult(ok=False, stderr="unexpected managed/policy settings: " + ", ".join(managed_policy))
        try:
            binary = _resolve_binary(str(cfg.get("command") or "claude"))
            actual_hash = _sha256(binary)
            actual_version = _version(binary)
            if actual_hash != EXPECTED_SHA256: raise ValueError(f"Claude Code binary SHA256 mismatch: {actual_hash}")
            if actual_version != EXPECTED_VERSION: raise ValueError(f"Claude Code version mismatch: {actual_version!r}")
            seed_dir = _path(str(cfg.get("benchmark_config_seed") or ""))
            seed_credential = _validate_seed(seed_dir)
            config_dir_resolved = Path(unicodedata.normalize("NFC", str(seed_dir.resolve())))
            plan_binding = {
                "plan_digest": str(cfg.get("evaluation_plan_digest") or ""),
                "benchmark_git_sha": str(cfg.get("benchmark_git_sha") or ""),
                "canonical_config_namespace": str(config_dir_resolved),
                "keychain_service": _keychain_service(config_dir_resolved),
                "binary": str(binary), "binary_sha256": actual_hash,
                "binary_version": actual_version, "model": model, "effort": effort,
            }
        except ValueError as exc:
            return AdapterRunResult(ok=False, stderr=str(exc))

        # CLAUDE_CONFIG_DIR is itself the OAuth namespace on macOS.  Using the
        # stable, dedicated seed as the canonical namespace avoids creating a
        # Keychain service/account per task.  The outer sandbox denies tools
        # this directory while the parent Claude process can refresh OAuth.
        config_dir = config_dir_resolved
        staged = ctx.sandbox / ".claude-benchmark" / _CREDENTIAL  # legacy cleanup probe; never created
        settings_dir = ctx.sandbox / ".claude-benchmark"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings = settings_dir / "benchmark-settings.json"
        settings_payload = {
            "hooks": {}, "enabledPlugins": {}, "extraKnownMarketplaces": {},
            "permissions": {
                "allow": [f"Read({ctx.workspace}/**)", f"Write({ctx.workspace}/**)", f"Edit({ctx.workspace}/**)",
                          f"Glob({ctx.workspace}/**)", f"Grep({ctx.workspace}/**)", "Bash"],
                "deny": [f"Read({seed_dir})", f"Read({seed_dir}/**)",
                         f"Read({_project_root()}/**)", f"Write({_project_root()}/**)",
                         f"Read({Path.home()}/.claude/**)",
                         "Read(/usr/bin/security)",
                         "Read(/System/Library/Frameworks/Security.framework/**)"],
                "additionalDirectories": [],
            },
            "sandbox": {
                "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False, "excludedCommands": [],
                "credentials": {
                    "files": [{"path": str(seed_credential), "mode": "deny"},
                              {"path": str(seed_dir), "mode": "deny"},
                              {"path": str(Path.home() / ".claude"), "mode": "deny"},
                              {"path": "/usr/bin/security", "mode": "deny"},
                              {"path": "/System/Library/Frameworks/Security.framework", "mode": "deny"}],
                    "envVars": [
                        {"name": "CLAUDE_CONFIG_DIR", "mode": "deny"},
                        {"name": "CLAUDE_SECURESTORAGE_CONFIG_DIR", "mode": "deny"},
                    ],
                },
                "filesystem": {
                    "allowWrite": [str(ctx.workspace)],
                    "allowRead": [str(ctx.workspace), str(ctx.sandbox), str(_project_root() / ".venv")],
                    "denyRead": [str(seed_dir), str(_project_root()), str(Path.home() / ".claude"),
                                 str(Path.home() / ".ssh"), str(Path.home() / ".aws"),
                                 str(Path.home() / ".config"), "/usr/bin/security",
                                 "/System/Library/Frameworks/Security.framework"],
                    "denyWrite": [str(seed_dir), str(_project_root()), str(Path.home() / ".claude")],
                },
            },
        }
        _atomic_json(settings, settings_payload)
        mcp = settings_dir / "empty-mcp.json"; mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        state_file = settings_dir / "adapter-state.json"
        try: state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        except (OSError, json.JSONDecodeError): state = {}
        if state and state.get("plan_binding") != plan_binding:
            _unlink(staged)
            return AdapterRunResult(ok=False, stderr="persisted Claude resume plan binding mismatch")
        if int(state.get("rounds", 0) or 0):
            artifacts = state.get("last_artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                return AdapterRunResult(ok=False, stderr="persisted Claude resume artifact manifest missing")
            try:
                for item in artifacts:
                    artifact = Path(str(item["path"]))
                    if artifact.is_symlink() or not artifact.is_file(): raise ValueError("missing/non-regular artifact")
                    artifact.resolve().relative_to(ctx.sandbox.resolve())
                    if _sha256(artifact) != item["sha256"]: raise ValueError("artifact hash mismatch")
            except (KeyError, TypeError, ValueError, OSError):
                return AdapterRunResult(ok=False, stderr="persisted Claude resume artifact validation failed")
        round_number = int(state.get("rounds", 0) or 0) + 1
        prior = str(state.get("session_id") or "")
        native_session = prior or str(uuid.uuid4())
        try: uuid.UUID(native_session)
        except ValueError:
            _unlink(staged); return AdapterRunResult(ok=False, stderr="invalid persisted Claude native session UUID")

        reserved = {"--model", "--effort", "--resume", "--session-id", "--output-format", "--input-format",
                    "--settings", "--setting-sources", "--mcp-config", "--strict-mcp-config", "--bare",
                    "--fallback-model", "--permission-mode", "--dangerously-skip-permissions", "--tools",
                    "--allowedTools", "--disallowedTools", "--system-prompt", "--append-system-prompt",
                    "--safe-mode", "--no-chrome", "--chrome", "--disable-slash-commands"}
        extras = [str(v) for v in cfg.get("extra_args", [])]
        if any(v in reserved or any(v.startswith(x + "=") for x in reserved) for v in extras):
            _unlink(staged); return AdapterRunResult(ok=False, stderr="reserved Claude Code extra_args are not allowed")
        cmd = [str(binary), "-p", "--verbose", "--output-format", "stream-json", "--model", model,
               "--effort", effort, "--permission-mode", "dontAsk", "--safe-mode", "--no-chrome",
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
                from harnessbench.macos_containment import (containment_paths, seatbelt_profile,
                                                            CONTAINMENT_CONTRACT, _capability_paths_from_env)
                sandbox_exec = Path("/usr/bin/sandbox-exec")
                if sys.platform != "darwin" or not sandbox_exec.is_file():
                    raise RuntimeError("reviewed macOS sandbox-exec containment is unavailable")
                read_write, write_only = containment_paths(
                    _project_root(), seed_dir, workspace=ctx.workspace, sandbox=ctx.sandbox,
                    binary=binary, capability_paths=_capability_paths_from_env(ctx.env))
                profile = seatbelt_profile(read_write, write_only)
                execution_cmd = [str(sandbox_exec), "-p", profile, *cmd]
                containment_contract = CONTAINMENT_CONTRACT
            except RuntimeError as exc:
                _unlink(staged); return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))
        stdout_log = ctx.sandbox / f"claude-round{round_number}.stdout.jsonl"
        stderr_log = ctx.sandbox / f"claude-round{round_number}.stderr.log"
        credential_hash_before = _sha256(seed_credential)
        timed_out = False
        try:
            with _namespace_lock(config_dir):
                proc = subprocess.Popen(execution_cmd, cwd=ctx.workspace, env=env, text=True,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=(os.name == "posix"))
                try:
                    stdout, stderr = proc.communicate(timeout=ctx.timeout_sec)
                    returncode = int(proc.returncode or 0)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    returncode = _terminate(proc, float(cfg.get("timeout_grace_sec", 5) or 5))
                    stdout, stderr = proc.communicate()
                # Refresh happens in-place in the single canonical namespace and
                # is covered by the same lock as the whole Claude invocation.
                seed_credential = _validate_seed(config_dir)
                credential_hash_after = _sha256(seed_credential)
        except (OSError, ValueError) as exc:
            return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))
        except BaseException:
            if "proc" in locals() and proc.poll() is None:
                try: _terminate(proc, 1)
                except Exception: pass
            raise
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")
        parse_error = ""
        try:
            rows = _parse_stream(stdout)
        except ValueError as exc:
            rows = _rows(stdout); parse_error = str(exc)
        init = next((r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"), {})
        results = [r for r in rows if r.get("type") == "result"]
        terminal = results[-1] if results else {}
        ids = {str(r.get("session_id")) for r in rows if r.get("session_id")}
        session_ok = ids == {native_session}
        disabled_fields_ok = all(init.get(key) == [] for key in ("mcp_servers", "plugins", "skills", "slash_commands"))
        # Fields which would prove customization escaped safe mode are rejected;
        # absent fields are accepted because 2.1.227 does not emit all of them.
        unexpected_effective = {key: init.get(key) for key in ("hooks", "agents", "commands")
                                if init.get(key) not in (None, [], {})}
        init_ok = (init.get("model") == model and init.get("claude_code_version") == "2.1.227"
                   and init.get("session_id") == native_session and init.get("permissionMode") == "dontAsk"
                   and disabled_fields_ok and not unexpected_effective)
        model_usage = terminal.get("modelUsage") if isinstance(terminal.get("modelUsage"), dict) else {}
        terminal_ok = (terminal.get("subtype") == "success" and terminal.get("is_error") is False and
                       terminal.get("session_id") == native_session and set(model_usage) == {model}
                       and isinstance(model_usage.get(model), dict))
        rate_limit_rows = [r for r in rows if r.get("type") == "rate_limit_event"]
        quota_censored = _quota_rejected(rows)
        trace = _write_trace(rows, ctx.sandbox / "usage-proxy", ctx, stdout_log, native_session, round_number)
        native_candidates = list(config_dir.rglob(f"{native_session}.jsonl"))
        retained_native = ctx.sandbox / "native-transcripts" / f"round-{round_number:02d}.jsonl"
        native_trace_ok = len(native_candidates) == 1
        native_hash = ""; normalized_hash = ""; normalization_error = ""
        if native_trace_ok:
            _atomic_copy(native_candidates[0], retained_native)
            try: _parse_native_transcript(retained_native, native_session)
            except (OSError, ValueError) as exc:
                native_trace_ok = False; normalization_error = str(exc)
            native_hash = _sha256(retained_native)
        try:
            normalized_hash = _normalize_trace(rows, ctx.sandbox / f"claude-round{round_number}.normalized.json",
                                               native_session=native_session, round_number=round_number,
                                               expected_model=model)
        except ValueError as exc:
            normalization_error = str(exc)
        ok = (returncode == 0 and not timed_out and not quota_censored and not parse_error and
              not normalization_error and session_ok and init_ok and terminal_ok and native_trace_ok and
              trace["response_count"] > 0 and trace["result_count"] == 1)
        normalized_path = ctx.sandbox / f"claude-round{round_number}.normalized.json"
        state_artifacts = [
            {"path": str(path), "sha256": digest}
            for path, digest in ((stdout_log, _sha256(stdout_log)), (stderr_log, _sha256(stderr_log)),
                                 (retained_native, native_hash), (normalized_path, normalized_hash))
            if path.is_file() and digest
        ] + trace["raw_response_artifacts"]
        _atomic_json(state_file, {"rounds": round_number, "session_id": native_session,
                                  "plan_binding": plan_binding, "last_artifacts": state_artifacts,
                                  "last_native_sha256": native_hash,
                                  "last_normalized_sha256": normalized_hash})
        return AdapterRunResult(ok=ok, command=cmd, stdout=stdout, stderr=stderr, metadata={
            "returncode": returncode, "timed_out": timed_out, "claude_version": actual_version,
            "binary": str(binary), "binary_sha256": actual_hash, "model": model, "actual_model": model,
            "effort": effort, "native_session_id": native_session, "round_number": round_number,
            "resumed": bool(prior), "session_ids_valid": session_ok, "init_valid": init_ok,
            "terminal_valid": terminal_ok, "disabled_features_valid": disabled_fields_ok,
            "unexpected_effective_init_fields": unexpected_effective, "quota_censored": quota_censored,
            "rate_limit_event_count": len(rate_limit_rows), "stream_parse_error": parse_error,
            "normalization_error": normalization_error,
            "native_session_file": str(retained_native) if native_trace_ok else "",
            "native_transcript_sha256": native_hash, "normalized_trace_sha256": normalized_hash,
            "stdout_log_file": str(stdout_log), "stdout_sha256": _sha256(stdout_log),
            "stderr_log_file": str(stderr_log), "stderr_sha256": _sha256(stderr_log),
            "raw_response_artifacts": trace["raw_response_artifacts"],
            "plan_binding": plan_binding, "evaluation_plan_digest": plan_binding["plan_digest"],
            "native_trace_dir": str(ctx.sandbox / "native-transcripts"), "synthetic_trace": trace,
            "staged_credential_removed": not staged.exists(),
            "credential_cleanup_proof": "no ephemeral namespace or per-task Keychain item created",
            "keychain_cleanup_proven": False, "canonical_config_namespace": str(config_dir),
            "expected_keychain_service": _keychain_service(config_dir),
            "refreshed_auth_synced": True, "credential_hash_before": credential_hash_before,
            "credential_hash_after": credential_hash_after, "benchmark_config_seed": str(seed_dir),
            "settings_sources": [], "safe_mode": True, "chrome_disabled": True, "mcp_disabled": True,
            "containment_contract": containment_contract, "execution_command": execution_cmd,
        })
