from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult

EXPECTED_CODEX_VERSION = "codex-cli 0.139.0"
_SUCCESS_EVENT = "turn.completed"
_FAILURE_EVENTS = {"turn.failed", "error"}
_ALLOWED_MODEL_CONFIG_KEYS = {
    "adapter", "command", "expected_version", "expected_executable_sha256",
    "expected_resolved_executable", "expected_native_executable", "expected_native_sha256",
    "benchmark_auth_file", "session_prefix", "timeout_sec", "timeout_grace_sec",
    "provider", "billing_mode", "model", "model_reasoning_effort", "sandbox",
    "sync_refreshed_auth", "stream_to_console", "allowed_hook_env", "sandbox_network_access", "use_usage_proxy",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_path(raw: str | Path) -> Path:
    path = Path(os.path.expanduser(str(raw)))
    return (path if path.is_absolute() else _project_root() / path).resolve()


def _resolve_command(command: str) -> Path | None:
    found = shutil.which(command)
    if not found:
        return None
    return Path(found).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _native_codex_binary(launcher: Path) -> Path | None:
    """Resolve the npm launcher's platform binary for complete provenance."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    triples = {
        ("darwin", "arm64"): ("codex-darwin-arm64", "aarch64-apple-darwin"),
        ("darwin", "x86_64"): ("codex-darwin-x64", "x86_64-apple-darwin"),
        ("linux", "aarch64"): ("codex-linux-arm64", "aarch64-unknown-linux-musl"),
        ("linux", "x86_64"): ("codex-linux-x64", "x86_64-unknown-linux-musl"),
    }
    package = triples.get((system, machine))
    if package is None:
        return None
    try:
        node_modules = next(parent for parent in launcher.parents if parent.name == "node_modules")
    except StopIteration:
        return None
    package_name, triple = package
    package_root = launcher.parent.parent
    candidates = [
        node_modules / "@openai" / package_name / "vendor" / triple / "bin" / "codex",
        package_root / "node_modules" / "@openai" / package_name / "vendor" / triple / "bin" / "codex",
        package_root / "vendor" / triple / "bin" / "codex",
    ]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _command_provenance(command: str, expected_version: str, expected_sha256: str = "", expected_resolved: str = "", expected_native: str = "", expected_native_sha256: str = "") -> dict[str, Any]:
    resolved = _resolve_command(command)
    result: dict[str, Any] = {
        "requested": command, "resolved": str(resolved or ""), "version": "",
        "sha256": "", "expected_version": expected_version,
        "expected_sha256": expected_sha256, "expected_resolved": expected_resolved,
        "expected_native_executable": expected_native, "expected_native_sha256": expected_native_sha256,
        "native_executable": "", "native_sha256": "", "valid": False,
    }
    if resolved is None or not resolved.is_file():
        result["error"] = "executable not found"
        return result
    try:
        completed = subprocess.run(
            [str(resolved), "--version"], text=True, capture_output=True,
            timeout=10, check=False,
            env={"PATH": os.environ.get("PATH", ""), "HOME": tempfile.gettempdir(),
                 "CODEX_HOME": tempfile.gettempdir(), "NO_COLOR": "1"},
        )
        output = (completed.stdout.strip() or completed.stderr.strip()).splitlines()
        version = output[0].strip() if completed.returncode == 0 and output else ""
        signature = _sha256(resolved)
        native = _native_codex_binary(resolved)
        if native is None and resolved.suffix.lower() != ".js":
            native = resolved  # direct native/test executable rather than npm launcher
        native_signature = _sha256(native) if native is not None else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        return result
    result.update(version=version, sha256=signature, native_executable=str(native or ""), native_sha256=native_signature)
    result["valid"] = (
        version == expected_version
        and (not expected_sha256 or signature.lower() == expected_sha256.lower())
        and (not expected_resolved or str(resolved) == expected_resolved)
        and (not expected_native or str(native or "") == expected_native)
        and (not expected_native_sha256 or native_signature.lower() == expected_native_sha256.lower())
    )
    if not result["valid"]:
        result["error"] = "Codex executable version/signature does not match the configured pin"
    return result


def _benchmark_auth_file(model_config: dict[str, Any]) -> Path | None:
    raw = str(model_config.get("benchmark_auth_file") or "").strip()
    return _resolve_path(raw) if raw else None


def _normal_host_auth_file() -> Path:
    return _resolve_path("~/.codex/auth.json")


def _is_dedicated_auth_file(path: Path | None) -> bool:
    if path is None:
        return False
    normal_home = _normal_host_auth_file().parent
    try:
        path.relative_to(normal_home)
        return False
    except ValueError:
        return True


def _read_regular_bytes(path: Path) -> bytes:
    """Read a regular file without following a final-component symlink."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError(f"refusing non-regular credential file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"credential file changed while opening: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _auth_kind_bytes(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(data, dict):
        return "invalid"
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and any(tokens.get(key) for key in ("access_token", "id_token", "refresh_token")):
        return "subscription_oauth"
    if data.get("OPENAI_API_KEY") or data.get("api_key"):
        return "api_key"
    return "unknown"


def _auth_kind(path: Path) -> str:
    try:
        return _auth_kind_bytes(_read_regular_bytes(path))
    except (OSError, ValueError):
        return "invalid"


@dataclass(frozen=True)
class StagedAuth:
    source: Path
    target: Path
    source_sha256: str



def _write_all_fd(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError("short credential write")
        written += count

def _stage_auth(source: Path, target_home: Path) -> StagedAuth:
    raw = _read_regular_bytes(source)
    if _auth_kind_bytes(raw) != "subscription_oauth":
        raise ValueError(f"source auth is not regular subscription OAuth state: {source}")
    target_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_stat = target_home.lstat()
    if target_home.is_symlink() or not stat.S_ISDIR(target_stat.st_mode):
        raise ValueError(f"refusing unsafe credential staging directory: {target_home}")
    target = target_home / "auth.json"
    if target.exists() or target.is_symlink():
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _write_all_fd(fd, raw)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _remove_auth(target)
        raise
    else:
        os.close(fd)
    return StagedAuth(source=source, target=target, source_sha256=hashlib.sha256(raw).hexdigest())



def provision_run_private_auth(seed: Path, destination: Path) -> dict[str, str]:
    """Create a run-private refreshable OAuth canonical file without modifying seed."""
    if not _is_dedicated_auth_file(seed):
        raise ValueError("normal host Codex auth cannot seed a Harness-Bench run")
    seed_raw = _read_regular_bytes(seed)
    if _auth_kind_bytes(seed_raw) != "subscription_oauth":
        raise ValueError("dedicated auth seed is not subscription OAuth")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_stat = destination.parent.lstat()
    if destination.parent.is_symlink() or not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("unsafe run-private auth directory")
    if destination.exists() or destination.is_symlink():
        current = _read_regular_bytes(destination)
        if _auth_kind_bytes(current) != "subscription_oauth":
            raise ValueError("existing run-private auth is invalid")
        return {"seed_sha256": hashlib.sha256(seed_raw).hexdigest(),
                "private_sha256": hashlib.sha256(current).hexdigest(), "created": "false"}
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _write_all_fd(fd, seed_raw); os.fsync(fd)
    except BaseException:
        os.close(fd); _remove_auth(destination); raise
    else:
        os.close(fd)
    return {"seed_sha256": hashlib.sha256(seed_raw).hexdigest(),
            "private_sha256": hashlib.sha256(seed_raw).hexdigest(), "created": "true"}

def _sync_staged_auth(stage: StagedAuth) -> tuple[bool, str]:
    """Persist only under our exclusive lock; refuse a detected unexpected change.

    This digest guard is not claimed as a filesystem-wide cross-process CAS for
    non-cooperating writers.
    """
    try:
        staged_raw = _read_regular_bytes(stage.target)
        if _auth_kind_bytes(staged_raw) != "subscription_oauth":
            return False, "staged_auth_invalid"
        if hashlib.sha256(staged_raw).hexdigest() == stage.source_sha256:
            return False, "unchanged"
        lock_path = stage.source.with_suffix(stage.source.suffix + ".harnessbench.lock")
        if lock_path.exists() and lock_path.is_symlink():
            return False, "lock_is_symlink"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current_raw = _read_regular_bytes(stage.source)
            if hashlib.sha256(current_raw).hexdigest() != stage.source_sha256:
                return False, "source_changed"
            if _auth_kind_bytes(current_raw) != "subscription_oauth":
                return False, "source_auth_invalid"
            fd, temporary = tempfile.mkstemp(prefix=stage.source.name + ".tmp.", dir=stage.source.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(staged_raw); out.flush(); os.fsync(out.fileno())
                # Recheck immediately before replacement while our runner lock is held.
                if hashlib.sha256(_read_regular_bytes(stage.source)).hexdigest() != stage.source_sha256:
                    return False, "source_changed"
                os.replace(temporary, stage.source)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return True, "updated"
    except (OSError, ValueError):
        return False, "io_or_safety_error"


def _remove_auth(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _acquire_auth_run_lock(source: Path):
    lock_path = source.with_suffix(source.suffix + ".run-exclusive.lock")
    if lock_path.is_symlink():
        raise ValueError(f"refusing symlink auth run lock: {lock_path}")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(fd, "a+", encoding="utf-8")
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise OSError("exclusive Codex auth locks require POSIX")
    except BaseException:
        handle.close()
        raise
    return handle


@contextmanager
def _staged_auth_lifetime(source: Path, target_home: Path):
    run_lock = _acquire_auth_run_lock(source)
    try:
        stage = _stage_auth(source, target_home)
        try:
            yield stage
        finally:
            _remove_auth(stage.target)
    finally:
        run_lock.close()


_FORBIDDEN_ENV_PARTS = ("TOKEN", "SECRET", "COOKIE", "CREDENTIAL", "PASSWORD", "PASSWD", "DSN", "API_KEY", "AUTH")
_BASE_ENV_ALLOWLIST = {"PATH", "TMPDIR", "TMP", "TEMP", "LANG", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT"}
_BENCH_ENV_ALLOWLIST = {"HARNESSBENCH_LLM_PROXY_URL", "HARNESSBENCH_LLM_PROXY_ROUTES"}
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_hook_env_names(raw: Any) -> tuple[list[str], bool]:
    if raw is None:
        return [], True
    if not isinstance(raw, list):
        return [], False
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
            return [], False
        if value in names:
            return [], False
        names.append(value)
    return names, True


def _sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in _FORBIDDEN_ENV_PARTS)


def _filtered_env(overrides: dict[str, str], approved_hook_vars: list[str] | None = None) -> tuple[dict[str, str], list[str]]:
    normalized, valid = _normalize_hook_env_names(approved_hook_vars)
    if not valid:
        raise ValueError("allowed_hook_env must be a list of unique, valid environment variable names")
    approved = set(normalized)
    unsafe_approved = sorted(name for name in approved if _sensitive_env_name(name))
    if unsafe_approved:
        raise ValueError(f"sensitive hook env names cannot be approved: {unsafe_approved}")
    env = {key: value for key, value in os.environ.items() if key in _BASE_ENV_ALLOWLIST or key.startswith("LC_")}
    for key in _BENCH_ENV_ALLOWLIST | approved:
        if key in overrides and not _sensitive_env_name(key):
            env[key] = str(overrides[key])
    removed = sorted(set(os.environ) - set(env))
    removed.extend(sorted(key for key in overrides if key not in env))
    return env, sorted(set(removed))


def _json_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows



def _json_diagnostics(text: str) -> dict[str, int]:
    nonempty = malformed = non_objects = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        nonempty += 1
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(value, dict):
            non_objects += 1
    return {"nonempty_lines": nonempty, "malformed_lines": malformed, "non_object_lines": non_objects}

def _session_id_from_rows(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        if row.get("type") == "thread.started":
            return str(row.get("thread_id") or row.get("thread", {}).get("id") or "")
    return ""


def _find_session_file(codex_home: Path, session_id: str) -> Path | None:
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return None
    candidates = sorted(sessions.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not session_id:
        return candidates[0] if candidates else None
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
                row = json.loads(line)
                payload = row.get("payload") if isinstance(row, dict) else None
                if isinstance(payload, dict) and str(payload.get("id") or payload.get("session_id") or "") == session_id:
                    return path
                if session_id in line:
                    return path
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _native_session_facts(path: Path | None) -> dict[str, Any]:
    facts: dict[str, Any] = {"provider": "", "models": [], "reasoning_efforts": [], "cli_version": "", "token_events": 0}
    if path is None or not path.is_file():
        return facts
    models: list[str] = []
    reasoning_efforts: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try: row = json.loads(line)
        except json.JSONDecodeError: continue
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict): continue
        if row.get("type") == "session_meta":
            facts["provider"] = str(payload.get("model_provider") or facts["provider"])
            facts["cli_version"] = str(payload.get("cli_version") or facts["cli_version"])
        elif row.get("type") == "turn_context":
            model = str(payload.get("model") or "")
            if model and model not in models: models.append(model)
            effort = str(payload.get("effort") or payload.get("reasoning_effort") or payload.get("model_reasoning_effort") or "")
            if effort and effort not in reasoning_efforts: reasoning_efforts.append(effort)
        elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
            facts["token_events"] += 1
    facts["models"] = models
    facts["reasoning_efforts"] = reasoning_efforts
    return facts


def _content_text(content: Any) -> str:
    if isinstance(content, str): return content
    if not isinstance(content, list): return ""
    return "\n".join(str(x.get("text")) for x in content if isinstance(x, dict) and isinstance(x.get("text"), str))


def _usage_values(usage: Any) -> dict[str, int]:
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    cached = int(usage.get("cached_input_tokens", 0) or 0)
    # cached_input_tokens is a subset of input_tokens in Codex/Responses usage.
    total = int(usage.get("total_tokens", 0) or input_tokens + output_tokens)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": cached, "cache_write_tokens": 0, "total_tokens": total}


def _next_number(directory: Path) -> int:
    values = []
    for path in directory.glob("codex-*.json"):
        try: values.append(int(path.stem.rsplit("-", 1)[-1]))
        except ValueError: pass
    return max(values, default=0) + 1


def write_codex_events_as_proxy_trace(*, stdout_text: str, stdout_log_file: Path,
    proxy_dir: Path, task_id: str, session_id: str, model_id: str,
    model: str, provider: str, initial_prompt: str, call_count: int = 1) -> dict[str, Any]:
    """Retain Codex's finalized JSON events and turn-level cache-inclusive usage."""
    responses = proxy_dir / "responses"; responses.mkdir(parents=True, exist_ok=True)
    requests_log = proxy_dir / "requests.jsonl"
    number = _next_number(responses)
    conversation: list[dict[str, Any]] = [{"role": "user", "content": initial_prompt}]
    response_paths: list[Path] = []
    completed = failed = False
    final_message_seen = False
    final_assistant_text = ""
    usage: dict[str, int] | None = None
    for row in _json_rows(stdout_text):
        row_type = str(row.get("type") or "")
        if row_type in _FAILURE_EVENTS: failed = True
        if row_type == _SUCCESS_EVENT:
            completed = True; usage = _usage_values(row.get("usage"))
        item = row.get("item")
        if row_type != "item.completed" or not isinstance(item, dict): continue
        item_type = str(item.get("type") or "")
        text = ""; tool_calls: list[dict[str, Any]] = []
        if item_type == "agent_message":
            text = str(item.get("text") or "")
            final_message_seen = final_message_seen or bool(text.strip())
            if text.strip():
                final_assistant_text = text
        elif item_type in {"command_execution", "mcp_tool_call", "web_search"}:
            name = "shell" if item_type == "command_execution" else item_type
            args = {k: item.get(k) for k in ("command", "status", "exit_code", "server", "tool", "query") if k in item}
            tool_calls = [{"id": str(item.get("id") or ""), "type": "function",
                           "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]
        else: continue
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls: message["tool_calls"] = tool_calls
        path = responses / f"codex-{number:04d}.json"
        path.write_text(json.dumps({"task_id": task_id, "session_id": session_id,
            "model_id": model_id, "framework": "codex", "provider": provider,
            "request_body": json.dumps({"messages": conversation}, ensure_ascii=False),
            "response_json": {"model": model, "choices": [{"message": message}]},
            "source_stdout_log_file": str(stdout_log_file), "native_event": row},
            ensure_ascii=False, indent=2), encoding="utf-8")
        response_paths.append(path); conversation.append(message); number += 1
        if item_type == "command_execution":
            output = str(item.get("aggregated_output") or "")
            conversation.append({"role": "tool", "tool_call_id": str(item.get("id") or ""), "content": output})
    if usage is not None:
        usage_row = {"task_id": task_id, "session_id": session_id, "model_id": model_id,
            "framework": "codex", "provider": provider,
            "raw_response_file": str(response_paths[-1]) if response_paths else "",
            "response_model": model, "call_count": max(1, int(call_count)), **usage}
        with requests_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(usage_row, ensure_ascii=False) + "\n")
    return {"proxy_dir": str(proxy_dir), "requests_log": str(requests_log),
            "response_count": len(response_paths), "turn_completed": completed,
            "failure_event_seen": failed, "final_assistant_seen": final_message_seen,
            "usage_available": usage is not None, "final_assistant_text": final_assistant_text,
            "stdout_json_diagnostics": _json_diagnostics(stdout_text)}


def _terminate_process_group(proc: subprocess.Popen[str], grace_sec: float) -> int:
    if proc.poll() is not None: return int(proc.returncode or 0)
    try:
        if os.name == "posix": os.killpg(proc.pid, signal.SIGTERM)
        else: proc.terminate()
        return proc.wait(timeout=max(.1, grace_sec))
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix": os.killpg(proc.pid, signal.SIGKILL)
            else: proc.kill()
        except ProcessLookupError: pass
        return proc.wait()



def _offline_argument_parse(executable: str, model: str, reasoning: str, network_access: bool = False) -> dict[str, Any]:
    """Ask clap to parse both invocation shapes with --help; never starts a turn."""
    with tempfile.TemporaryDirectory(prefix="harnessbench-codex-parse-") as tmp:
        root = Path(tmp); output = root / "last.txt"
        common = ["--json", "--model", model, "--strict-config", "--ignore-user-config",
                  "--ignore-rules", "--config", f"model_reasoning_effort={json.dumps(reasoning)}",
                  "--config", f"sandbox_workspace_write.network_access={json.dumps(network_access)}",
                  "--output-last-message", str(output)]
        commands = [
            [executable, "exec", "--cd", str(root), "--skip-git-repo-check",
             "--sandbox", "workspace-write", *common, "--help"],
            [executable, "exec", "resume", *common, "--help"],
        ]
        results = []
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root),
               "CODEX_HOME": str(root / ".codex"), "NO_COLOR": "1"}
        for command in commands:
            try:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=10,
                                           check=False, env=env)
                results.append({"returncode": completed.returncode,
                    "help_seen": "Usage:" in completed.stdout or "Usage:" in completed.stderr,
                    "stderr": completed.stderr[:500]})
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append({"returncode": None, "help_seen": False, "stderr": str(exc)})
    return {"ok": all(item["returncode"] == 0 and item["help_seen"] for item in results),
            "results": results}

def codex_preflight(model_config: dict[str, Any]) -> dict[str, Any]:
    model = str(model_config.get("model") or "gpt-5.4").strip()
    reasoning = str(model_config.get("model_reasoning_effort") or "medium").strip()
    provider = str(model_config.get("provider") or "openai").strip()
    billing = str(model_config.get("billing_mode") or "subscription_oauth").strip()
    expected_version = str(model_config.get("expected_version") or EXPECTED_CODEX_VERSION).strip()
    network_raw = model_config.get("sandbox_network_access", False)
    network_config_valid = isinstance(network_raw, bool)
    network_access = network_raw if network_config_valid else False
    unknown_keys = sorted(set(model_config) - _ALLOWED_MODEL_CONFIG_KEYS)
    controls_ok = not unknown_keys and not model_config.get("extra_args") and not model_config.get("config_overrides")
    provenance = _command_provenance(
        str(model_config.get("command") or "codex"), expected_version,
        str(model_config.get("expected_executable_sha256") or "").strip(),
        str(model_config.get("expected_resolved_executable") or "").strip(),
        str(model_config.get("expected_native_executable") or "").strip(),
        str(model_config.get("expected_native_sha256") or "").strip(),
    )
    parse = _offline_argument_parse(str(provenance.get("resolved") or model_config.get("command") or "codex"), model, reasoning, network_access) if provenance.get("valid") else {"ok": False, "results": []}
    auth = _benchmark_auth_file(model_config)
    dedicated_auth = _is_dedicated_auth_file(auth)
    auth_kind = _auth_kind(auth) if dedicated_auth and auth is not None else ("rejected_normal_host_path" if auth is not None else "missing")
    approved_hook_env, hook_env_valid = _normalize_hook_env_names(model_config.get("allowed_hook_env"))
    hook_env_safe = hook_env_valid and not any(_sensitive_env_name(name) for name in approved_hook_env)
    checks = {
        "executable": bool(provenance.get("valid")),
        "native_binary": bool(provenance.get("native_executable") and provenance.get("native_sha256")),
        "offline_argument_parse": bool(parse["ok"]),
        "expected_version_pin": expected_version == EXPECTED_CODEX_VERSION,
        "model_pin": model == "gpt-5.4", "reasoning_pin": reasoning == "medium",
        "provider_pin": provider == "openai", "billing_pin": billing == "subscription_oauth",
        "sandbox_pin": str(model_config.get("sandbox") or "workspace-write") == "workspace-write",
        "sandbox_network_config_valid": network_config_valid,
        "controls_allowlisted": controls_ok, "hook_env_valid": hook_env_valid, "hook_env_safe": hook_env_safe,
        "dedicated_auth_path": dedicated_auth,
        "auth_exists": bool(dedicated_auth and auth is not None and auth.is_file() and not auth.is_symlink()),
        "subscription_oauth": auth_kind == "subscription_oauth",
    }
    return {"ok": all(checks.values()), "checks": checks, "provenance": provenance,
            "argument_parse": parse, "unknown_config_keys": unknown_keys,
            "benchmark_auth_file": str(auth or ""), "normal_host_auth_file": str(_normal_host_auth_file()),
            "auth_kind": auth_kind, "allowed_hook_env": approved_hook_env,
            "sandbox_network_access": network_access,
            "model": model, "reasoning": reasoning, "provider": provider, "billing_mode": billing}


class CodexAdapter(BaseAdapter):
    name = "codex"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        preflight = codex_preflight(ctx.model_config)
        if not preflight["ok"]:
            return AdapterRunResult(ok=False, stderr="Codex preflight failed", metadata={"preflight": preflight})
        model = preflight["model"]; reasoning = preflight["reasoning"]
        provider = preflight["provider"]; billing = preflight["billing_mode"]
        source_auth = Path(preflight["benchmark_auth_file"])
        codex_home = ctx.sandbox / ".codex"; codex_home.mkdir(parents=True, exist_ok=True)
        try:
            auth_context = _staged_auth_lifetime(source_auth, codex_home)
            auth_stage = auth_context.__enter__()
        except (OSError, ValueError) as exc:
            return AdapterRunResult(ok=False, stderr=f"unsafe Codex auth state: {exc}", metadata={"preflight": preflight})
        try:
            state_file = codex_home / "harnessbench-state.json"; state = _read_state(state_file)
            round_number = int(state.get("rounds", 0) or 0) + 1
            prior_session = str(state.get("session_id") or "").strip()
            executable = str(preflight["provenance"]["resolved"])
            common = ["--json", "--model", model, "--strict-config", "--ignore-user-config", "--ignore-rules",
                      "--config", f"model_reasoning_effort={json.dumps(reasoning)}",
                      "--config", f"sandbox_workspace_write.network_access={json.dumps(preflight['sandbox_network_access'])}"]
            last_message = ctx.sandbox / f"codex-round{round_number}.last-message.txt"
            if prior_session:
                cmd = [executable, "exec", "resume", *common, "--output-last-message", str(last_message),
                       prior_session, "-"]
            else:
                cmd = [executable, "exec", "--cd", str(ctx.workspace), "--skip-git-repo-check",
                       "--sandbox", "workspace-write", *common,
                       "--output-last-message", str(last_message), "-"]
            env, removed_secrets = _filtered_env(ctx.env, preflight["allowed_hook_env"])
            env.update({"HOME": str(ctx.sandbox), "CODEX_HOME": str(codex_home), "NO_COLOR": "1",
                "CODEX_TELEMETRY_DISABLED": "1", "WORKSPACE": str(ctx.workspace),
                "HARNESSBENCH_TASK_ID": ctx.task.task_id, "HARNESSBENCH_WORKSPACE": str(ctx.workspace),
                "HARNESSBENCH_SANDBOX": str(ctx.sandbox), "HARNESSBENCH_SESSION_ID": ctx.session_id,
                "HARNESSBENCH_PROMPT_FILE": str(ctx.prompt_file), "HARNESSBENCH_MODEL_ID": ctx.model_id})
            env.pop("FORCE_COLOR", None)
            stdout_log = ctx.sandbox / f"codex-round{round_number}.stdout.jsonl"
            stderr_log = ctx.sandbox / f"codex-round{round_number}.stderr.log"
            stdout_chunks: list[str] = []; stderr_chunks: list[str] = []
            proc: subprocess.Popen[str] | None = None; timed_out = False
            try:
                proc = subprocess.Popen(cmd, cwd=str(ctx.workspace), text=True, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=1,
                    start_new_session=(os.name == "posix"))
                def reader(pipe: TextIO | None, sink: list[str], path: Path, mirror: TextIO | None) -> None:
                    try:
                        assert pipe is not None
                        with path.open("w", encoding="utf-8", buffering=1) as handle:
                            for line in iter(pipe.readline, ""):
                                sink.append(line); handle.write(line)
                                if mirror: mirror.write(line); mirror.flush()
                    finally:
                        if pipe: pipe.close()
                mirror = bool(ctx.model_config.get("stream_to_console", False))
                out_thread = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks, stdout_log, sys.stdout if mirror else None), daemon=True)
                err_thread = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks, stderr_log, sys.stderr if mirror else None), daemon=True)
                out_thread.start(); err_thread.start()
                if proc.stdin:
                    try: proc.stdin.write(ctx.prompt); proc.stdin.close()
                    except BrokenPipeError: pass
                try: returncode = proc.wait(timeout=ctx.timeout_sec)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    returncode = _terminate_process_group(proc, float(ctx.model_config.get("timeout_grace_sec", 5) or 5))
                out_thread.join(timeout=2 if timed_out else None); err_thread.join(timeout=2 if timed_out else None)
            except OSError as exc:
                _remove_auth(auth_stage.target)
                return AdapterRunResult(ok=False, command=cmd, stderr=str(exc), metadata={"preflight": preflight})
            except BaseException:
                if proc is not None and proc.poll() is None: _terminate_process_group(proc, 1)
                _remove_auth(auth_stage.target); raise
            stdout = "".join(stdout_chunks); stderr = "".join(stderr_chunks); rows = _json_rows(stdout)
            native_session_id = _session_id_from_rows(rows) or prior_session
            session_file = _find_session_file(codex_home, native_session_id)
            facts = _native_session_facts(session_file)
            native_call_count = int(facts["token_events"]) - int(state.get("native_token_events", 0) or 0)
            trace = write_codex_events_as_proxy_trace(stdout_text=stdout, stdout_log_file=stdout_log,
                proxy_dir=ctx.sandbox / "usage-proxy", task_id=ctx.task.task_id, session_id=ctx.session_id,
                model_id=ctx.model_id, model=model, provider=provider, initial_prompt=ctx.prompt,
                call_count=native_call_count)
            provider_ok = facts["provider"] in {"openai", "openai-codex"}
            model_ok = bool(facts["models"]) and set(facts["models"]) == {model}
            billing_ok = _auth_kind(auth_stage.target) == billing
            reasoning_ok = bool(facts["reasoning_efforts"]) and set(facts["reasoning_efforts"]) == {reasoning}
            version_ok = facts["cli_version"] == "0.139.0"
            stdout_json_ok = trace["stdout_json_diagnostics"]["malformed_lines"] == 0 and trace["stdout_json_diagnostics"]["non_object_lines"] == 0
            try:
                final_message_text = last_message.read_text(encoding="utf-8")
            except OSError:
                final_message_text = ""
            last_message_ok = bool(final_message_text.strip()) and final_message_text.strip() == str(trace["final_assistant_text"]).strip()
            call_count_ok = 1 <= native_call_count <= 10_000
            ok = (returncode == 0 and not timed_out and bool(native_session_id) and session_file is not None
                  and trace["turn_completed"] and not trace["failure_event_seen"]
                  and trace["final_assistant_seen"] and trace["usage_available"]
                  and stdout_json_ok and last_message_ok and call_count_ok
                  and provider_ok and model_ok and reasoning_ok and billing_ok and version_ok)
            if native_session_id:
                tmp = state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps({"session_id": native_session_id, "rounds": round_number, "native_token_events": int(facts["token_events"])}, indent=2) + "\n", encoding="utf-8")
                tmp.replace(state_file)
            auth_synced = False
            auth_sync_status = "disabled"
            if bool(ctx.model_config.get("sync_refreshed_auth", True)):
                auth_synced, auth_sync_status = _sync_staged_auth(auth_stage)
            return AdapterRunResult(ok=ok, command=cmd, stdout=stdout, stderr=stderr, metadata={
                "returncode": returncode, "timed_out": timed_out, "preflight": preflight,
                "executable_provenance": preflight["provenance"], "provider": provider, "model": model,
                "model_reasoning_effort": reasoning, "billing_mode": billing, "provider_ok": provider_ok,
                "model_ok": model_ok, "reasoning_ok": reasoning_ok, "billing_ok": billing_ok, "native_version_ok": version_ok,
                "stdout_json_ok": stdout_json_ok, "last_message_ok": last_message_ok, "call_count_ok": call_count_ok,
                "codex_home": str(codex_home), "state_dir": str(codex_home),
                "benchmark_auth_file": str(source_auth),
                "native_session_id": native_session_id, "codex_session_file": str(session_file or ""),
                "native_session_facts": facts, "native_call_count": native_call_count, "round_number": round_number, "resumed": bool(prior_session),
                "stdout_log_file": str(stdout_log), "stderr_log_file": str(stderr_log),
                "last_message_file": str(last_message), "synthetic_proxy_trace": trace,
                "filtered_host_secret_names": removed_secrets, "auth_synced": auth_synced, "auth_sync_status": auth_sync_status,
                "staged_auth_removed": True, "credentials_retained_in_sandbox": False,
                "workspace": str(ctx.workspace)})
        finally:
            auth_context.__exit__(None, None, None)
