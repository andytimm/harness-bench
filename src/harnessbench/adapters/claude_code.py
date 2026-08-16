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
import ctypes
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult

EXPECTED_VERSION = "2.1.227 (Claude Code)"
EXPECTED_SHA256 = "7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7"
EXPECTED_MODEL = "claude-opus-4-6"
EXPECTED_EFFORT = "medium"
_SECRET_SUFFIXES = ("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET")
CLAUDE_PLAN_BINDING_KEYS = (
    "plan_digest", "benchmark_git_sha", "canonical_benchmark_seed",
    "canonical_config_namespace", "auth_backend", "keychain_service",
    "keychain_status_semantics", "auth_status_semantics", "binary",
    "binary_version", "binary_sha256", "model", "effort",
)


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
    """Validate the dedicated Keychain namespace without reading auth values."""
    home = Path.home().resolve()
    normal = (home / ".claude").resolve()
    resolved = Path(unicodedata.normalize("NFC", str(seed.expanduser().resolve())))
    if resolved == home:
        raise ValueError("benchmark_config_seed must not use the normal home directory")
    if resolved == normal or normal in resolved.parents:
        raise ValueError("benchmark_config_seed must not use normal ~/.claude authentication")
    _assert_secure_path(seed, directory=True)
    forbidden = [resolved / name for name in ("settings.json", "settings.local.json", "CLAUDE.md",
                 "commands", "agents", "plugins", "skills", "hooks", ".credentials.json")]
    present = [str(path) for path in forbidden if path.exists() or path.is_symlink()]
    if present:
        raise ValueError("dedicated Claude namespace contains customization or plaintext credentials: " + ", ".join(present))
    return resolved


def _keychain_status(service: str) -> int:
    """Return only exact-service lookup status; password outputs are always NULL."""
    if sys.platform != "darwin":
        raise ValueError("macOS Security.framework Keychain authentication is required")
    try:
        fn = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Versions/A/Security"
        ).SecKeychainFindGenericPassword
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                       ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p]
        fn.restype = ctypes.c_int32
        encoded = service.encode("utf-8")
        return int(fn(None, len(encoded), encoded, 0, None, None, None, None))
    except (OSError, AttributeError) as exc:
        raise ValueError("Security.framework exact-service status lookup unavailable") from exc


_PAID_SUBSCRIPTIONS = frozenset({"pro", "max", "team", "enterprise"})
AUTH_BACKEND = "macos-security-framework-generic-password"
KEYCHAIN_STATUS_SEMANTICS = "SecKeychainFindGenericPassword-exact-service-NULL-password-outputs"
AUTH_STATUS_SEMANTICS = "claude-auth-status-json:loggedIn+claude.ai+firstParty+paid"


def _validate_auth_status(binary: Path, env: dict[str, str]) -> int:
    """Validate pinned Claude status while discarding all output and identity fields."""
    try:
        completed = subprocess.run([str(binary), "auth", "status", "--json"], env=env,
            text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Claude authentication status validation failed") from exc
    code = int(completed.returncode)
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Claude authentication status is malformed") from None
    # Deliberately extract only authorization properties. Other fields may identify
    # the subscriber and are captured by subprocess only long enough to discard.
    valid = (code == 0 and isinstance(value, dict)
             and value.get("loggedIn") is True
             and value.get("authMethod") == "claude.ai"
             and value.get("apiProvider") == "firstParty"
             and value.get("subscriptionType") in _PAID_SUBSCRIPTIONS)
    if not valid:
        raise ValueError("Claude authentication status does not match paid first-party claude.ai")
    return code


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
    canonical_config = Path(unicodedata.normalize("NFC", str(config_dir.resolve())))
    normal_home = Path.home().resolve()
    normal_claude = (normal_home / ".claude").resolve()
    if canonical_config == normal_home or canonical_config == normal_claude or normal_claude in canonical_config.parents:
        raise ValueError("Claude config must be an isolated canonical namespace")
    keep = {"PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "SHELL", "TERM", "USER", "LOGNAME", "SSH_TTY"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    # Hook-provided values are task capabilities, not host credentials.
    env.update(overrides)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(_SECRET_SUFFIXES) or upper.startswith(("AWS_SECRET_", "AZURE_CLIENT_SECRET")):
            env.pop(key, None)
    env.update({
        # macOS Security.framework default-Keychain discovery requires the real
        # login HOME. Native credential env filtering removes HOME from Bash.
        "HOME": str(normal_home), "CLAUDE_CONFIG_DIR": str(canonical_config),
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(canonical_config), "NO_COLOR": "1",
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
            config_dir_resolved = _validate_seed(seed_dir)
            canonical_seed = str(config_dir_resolved)
            plan_binding = {
                "plan_digest": str(cfg.get("evaluation_plan_digest") or ""),
                "benchmark_git_sha": str(cfg.get("benchmark_git_sha") or ""),
                "canonical_benchmark_seed": canonical_seed,
                "canonical_config_namespace": canonical_seed,
                "auth_backend": AUTH_BACKEND,
                "keychain_service": _keychain_service(config_dir_resolved),
                "keychain_status_semantics": KEYCHAIN_STATUS_SEMANTICS,
                "auth_status_semantics": AUTH_STATUS_SEMANTICS,
                "binary": str(binary), "binary_version": actual_version,
                "binary_sha256": actual_hash, "model": model, "effort": effort,
            }
            if tuple(plan_binding) != CLAUDE_PLAN_BINDING_KEYS:
                raise ValueError("internal Claude plan binding schema mismatch")
            for key in ("canonical_benchmark_seed", "canonical_config_namespace", "auth_backend", "keychain_service", "keychain_status_semantics", "auth_status_semantics"):
                supplied = cfg.get(key)
                if supplied is not None and str(supplied) != plan_binding[key]:
                    raise ValueError(f"Claude plan binding {key} mismatch")
            expected_binding = cfg.get("evaluation_plan_binding")
            if expected_binding is not None and expected_binding != plan_binding:
                raise ValueError("Claude evaluation plan binding mismatch")
        except ValueError as exc:
            return AdapterRunResult(ok=False, stderr=str(exc))

        # Claude remains outside its tool sandbox solely to use the dedicated
        # namespaced OAuth identity.  Its native macOS Bash sandbox is the sole
        # OS boundary for Bash and descendants; in-process file tools are
        # separately constrained by explicit permission rules.
        config_dir = config_dir_resolved
        settings_dir = ctx.sandbox / ".claude-benchmark"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings = settings_dir / "benchmark-settings.json"
        from harnessbench.macos_containment import (builtin_permission_denies,
            builtin_sensitive_paths, native_credential_paths,
            validate_builtin_permission_denies, validate_native_policy_shape,
            native_sandbox_policy)
        try:
            capability_paths=[Path(v) for k,v in ctx.env.items()
                              if k.endswith(("_FILE","_DIR","_PATH")) and v and Path(v).is_absolute()]
            control_paths=[Path(v) for v in cfg.get("containment_control_roots",[])]
            filesystem = native_sandbox_policy(_project_root(), seed_dir, workspace=ctx.workspace,
                sandbox=ctx.sandbox, binary=binary, capability_paths=capability_paths,
                control_paths=control_paths)
            sensitive = builtin_sensitive_paths(_project_root(), seed_dir,
                workspace=ctx.workspace, control_paths=control_paths)
            credential_sensitive = native_credential_paths(seed_dir,
                workspace=ctx.workspace, control_paths=control_paths)
        except (OSError, RuntimeError) as exc:
            return AdapterRunResult(ok=False, stderr=str(exc))
        permission_denies = builtin_permission_denies(sensitive)
        settings_payload = {
            "hooks": {}, "enabledPlugins": {}, "extraKnownMarketplaces": {},
            "permissions": {
                "allow": [f"Read({ctx.workspace}/**)", f"Write({ctx.workspace}/**)",
                          f"Edit({ctx.workspace}/**)", f"Glob({ctx.workspace}/**)",
                          f"Grep({ctx.workspace}/**)", "Bash"],
                "deny": permission_denies, "additionalDirectories": [],
            },
            "sandbox": {
                "enabled": True, "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
                "excludedCommands": [], "filesystem": filesystem,
                "credentials": {
                    "files": [{"path": value, "mode": "deny"} for value in credential_sensitive],
                    "envVars": [{"name": "HOME", "mode": "deny"},
                                {"name": "CLAUDE_CONFIG_DIR", "mode": "deny"},
                                {"name": "CLAUDE_SECURESTORAGE_CONFIG_DIR", "mode": "deny"}],
                },
            },
        }
        # Pin the exact shape before launch.  Claude -p silently ignores invalid
        # settings, so runtime init/tool evidence below is also mandatory.
        if (settings_payload["sandbox"].get("enabled") is not True or
            settings_payload["sandbox"].get("failIfUnavailable") is not True or
            settings_payload["sandbox"].get("allowUnsandboxedCommands") is not False or
            not validate_native_policy_shape(filesystem, credential_sensitive) or
            str(ctx.workspace.resolve()) not in filesystem["allowRead"] or
            not validate_builtin_permission_denies(sensitive, permission_denies)):
            return AdapterRunResult(ok=False, stderr="native sandbox settings invariant failed")
        _atomic_json(settings, settings_payload)
        mcp = settings_dir / "empty-mcp.json"; mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        state_file = settings_dir / "adapter-state.json"
        try: state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        except (OSError, json.JSONDecodeError): state = {}
        if state and state.get("plan_binding") != plan_binding:
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
            return AdapterRunResult(ok=False, stderr="invalid persisted Claude native session UUID")

        reserved = {"--model", "--effort", "--resume", "--session-id", "--output-format", "--input-format",
                    "--settings", "--setting-sources", "--mcp-config", "--strict-mcp-config", "--bare",
                    "--fallback-model", "--permission-mode", "--dangerously-skip-permissions", "--tools",
                    "--allowedTools", "--disallowedTools", "--system-prompt", "--append-system-prompt",
                    "--safe-mode", "--no-chrome", "--chrome", "--disable-slash-commands"}
        extras = [str(v) for v in cfg.get("extra_args", [])]
        if any(v in reserved or any(v.startswith(x + "=") for x in reserved) for v in extras):
            return AdapterRunResult(ok=False, stderr="reserved Claude Code extra_args are not allowed")
        cmd = [str(binary), "-p", "--verbose", "--output-format", "stream-json", "--model", model,
               "--effort", effort, "--permission-mode", "dontAsk", "--safe-mode", "--no-chrome",
               "--disable-slash-commands", "--setting-sources", "", "--settings", str(settings),
               "--strict-mcp-config", "--mcp-config", str(mcp),
               "--tools", ",".join(("Read","Edit","Write","Glob","Grep","Bash"))]
        if prior: cmd += ["--resume", native_session]
        else: cmd += ["--session-id", native_session]
        cmd += extras + [ctx.prompt]
        env = _clean_env(ctx.env, config_dir, ctx)
        # Never wrap Claude in sandbox-exec: 2.1.227 must create its one native
        # sandbox itself, and Seatbelt cannot be safely nested.
        execution_cmd = cmd
        containment_contract = "claude-native-macos-bash-sandbox-plus-builtin-permission-denies"
        if bool(cfg.get("require_macos_containment", False)) and sys.platform != "darwin":
            return AdapterRunResult(ok=False, command=cmd, stderr="Claude native macOS sandbox is required")
        stdout_log = ctx.sandbox / f"claude-round{round_number}.stdout.jsonl"
        stderr_log = ctx.sandbox / f"claude-round{round_number}.stderr.log"
        timed_out = False
        try:
            # One symlink-safe namespace lock covers the status-only preflight,
            # Claude invocation, and unchanged exact-service postcondition.
            with _namespace_lock(config_dir):
                _validate_seed(config_dir)
                keychain_status_before = _keychain_status(plan_binding["keychain_service"])
                keychain_exists_before = keychain_status_before == 0
                if not keychain_exists_before:
                    raise ValueError("dedicated Claude Keychain service is unavailable")
                auth_status_code = _validate_auth_status(binary, env)
                try:
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
                finally:
                    keychain_status_after = _keychain_status(plan_binding["keychain_service"])
                    keychain_exists_after = keychain_status_after == 0
                    if ((keychain_status_after, keychain_exists_after) !=
                        (keychain_status_before, keychain_exists_before)):
                        raise ValueError("dedicated Claude Keychain exact-service status changed")
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
        expected_tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]
        init_tools = init.get("tools")
        tool_list_ok = (isinstance(init_tools, list) and len(init_tools) == len(expected_tools)
                        and set(init_tools) == set(expected_tools))
        # Fields which would prove customization escaped safe mode are rejected;
        # absent fields are accepted because 2.1.227 does not emit all of them.
        inert_agents = ["claude", "Explore", "general-purpose", "Plan"]
        agents_ok = init.get("agents") in (None, [], inert_agents)
        unexpected_effective = {key: init.get(key) for key in ("hooks", "commands")
                                if init.get(key) not in (None, [], {})}
        init_ok = (init.get("model") == model and init.get("claude_code_version") == "2.1.227"
                   and init.get("session_id") == native_session and init.get("permissionMode") == "dontAsk"
                   and disabled_fields_ok and tool_list_ok and agents_ok
                   and not any(tool in init_tools for tool in ("Agent", "Task"))
                   and not unexpected_effective)
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
            "unexpected_effective_init_fields": unexpected_effective, "inert_agents_valid": agents_ok,
            "init_agents": init.get("agents"), "quota_censored": quota_censored,
            "rate_limit_event_count": len(rate_limit_rows), "stream_parse_error": parse_error,
            "normalization_error": normalization_error,
            "native_session_file": str(retained_native) if native_trace_ok else "",
            "native_transcript_sha256": native_hash, "normalized_trace_sha256": normalized_hash,
            "stdout_log_file": str(stdout_log), "stdout_sha256": _sha256(stdout_log),
            "stderr_log_file": str(stderr_log), "stderr_sha256": _sha256(stderr_log),
            "raw_response_artifacts": trace["raw_response_artifacts"],
            "plan_binding": plan_binding, "evaluation_plan_digest": plan_binding["plan_digest"],
            "native_trace_dir": str(ctx.sandbox / "native-transcripts"), "synthetic_trace": trace,
            "auth_backend": AUTH_BACKEND, "canonical_config_namespace": str(config_dir),
            "keychain_service": plan_binding["keychain_service"],
            "keychain_status_semantics": KEYCHAIN_STATUS_SEMANTICS,
            "auth_status_semantics": AUTH_STATUS_SEMANTICS,
            "keychain_status_before": keychain_status_before,
            "keychain_status_after": keychain_status_after,
            "keychain_exists_before": keychain_exists_before,
            "keychain_exists_after": keychain_exists_after,
            "keychain_status_unchanged": True, "auth_status_code": auth_status_code,
            "auth_status_valid": True, "benchmark_config_seed": str(seed_dir),
            "settings_sources": [], "safe_mode": True, "chrome_disabled": True, "mcp_disabled": True,
            "native_sandbox_settings_valid": True, "native_sandbox_runtime_evidence": False,
            "builtin_file_tool_denies_valid": validate_builtin_permission_denies(sensitive, permission_denies),
            "exposed_tools": expected_tools, "init_tools": init_tools, "tool_list_valid": tool_list_ok,
            "containment_contract": containment_contract, "execution_command": execution_cmd,
        })
