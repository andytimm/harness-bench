from __future__ import annotations

import hashlib
import json
import os
import re
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, TextIO

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult

EXPECTED_CODEX_VERSION = "codex-cli 0.139.0"
_SUCCESS_EVENT = "turn.completed"
_FAILURE_EVENTS = {"turn.failed", "error"}
_SECRET_NAMES = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    "SSH_AUTH_SOCK", "GOOGLE_APPLICATION_CREDENTIALS", "DATABASE_URL",
}
_RESERVED_CONFIG_KEYS = {"model", "model_reasoning_effort", "model_provider"}


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


def _command_provenance(command: str, expected_version: str, expected_sha256: str = "") -> dict[str, Any]:
    resolved = _resolve_command(command)
    result: dict[str, Any] = {
        "requested": command, "resolved": str(resolved or ""), "version": "",
        "sha256": "", "expected_version": expected_version,
        "expected_sha256": expected_sha256, "native_executable": "", "native_sha256": "", "valid": False,
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
        native_signature = _sha256(native) if native is not None else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        return result
    result.update(version=version, sha256=signature, native_executable=str(native or ""), native_sha256=native_signature)
    result["valid"] = version == expected_version and (
        not expected_sha256 or signature.lower() == expected_sha256.lower()
    )
    if not result["valid"]:
        result["error"] = "Codex executable version/signature does not match the configured pin"
    return result


def _source_codex_home(model_config: dict[str, Any]) -> Path:
    explicit = str(model_config.get("user_codex_home") or "").strip()
    if explicit:
        return _resolve_path(explicit)
    legacy = str(model_config.get("user_config") or "").strip()
    if legacy:
        return _resolve_path(legacy).parent
    return _resolve_path("~/.codex")


def _auth_kind(path: Path) -> str:
    """Classify auth without retaining or reporting credential values."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(data, dict):
        return "invalid"
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and any(tokens.get(key) for key in ("access_token", "id_token", "refresh_token")):
        return "subscription_oauth"
    if data.get("OPENAI_API_KEY") or data.get("api_key"):
        return "api_key"
    return "unknown"


def _stage_auth(source: Path, target_home: Path) -> Path:
    target_home.mkdir(parents=True, exist_ok=True)
    target = target_home / "auth.json"
    shutil.copy2(source, target)
    target.chmod(0o600)
    return target


def _sync_staged_auth(source: Path, staged: Path) -> bool:
    """Atomically persist a refresh made by Codex while keeping the sandbox copy isolated."""
    if not staged.is_file():
        return False
    try:
        # Refuse to replace a subscription source with a malformed or different auth mode.
        if _auth_kind(source) != "subscription_oauth" or _auth_kind(staged) != "subscription_oauth":
            return False
        if source.read_bytes() == staged.read_bytes():
            return False
        lock_path = source.with_suffix(source.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            fd, temporary = tempfile.mkstemp(prefix=source.name + ".tmp.", dir=source.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as out, staged.open("rb") as inp:
                    shutil.copyfileobj(inp, out)
                    out.flush(); os.fsync(out.fileno())
                os.replace(temporary, source)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return True
    except OSError:
        return False


def _remove_auth(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _filtered_env(overrides: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    removed: list[str] = []
    for key in list(env):
        upper = key.upper()
        if (
            key in _SECRET_NAMES
            or upper.endswith(("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET"))
            or upper.startswith(("AWS_SECRET_", "AZURE_CLIENT_SECRET"))
        ):
            removed.append(key); env.pop(key, None)
    # Runtime hook variables are benchmark inputs and deliberately overlaid after filtering.
    env.update(overrides)
    return env, sorted(removed)


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
            "usage_available": usage is not None}


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


def codex_preflight(model_config: dict[str, Any]) -> dict[str, Any]:
    model = str(model_config.get("model") or "gpt-5.4").strip()
    reasoning = str(model_config.get("model_reasoning_effort") or "medium").strip()
    provider = str(model_config.get("provider") or "openai").strip()
    billing = str(model_config.get("billing_mode") or "subscription_oauth").strip()
    expected_version = str(model_config.get("expected_version") or EXPECTED_CODEX_VERSION).strip()
    provenance = _command_provenance(str(model_config.get("command") or "codex"), expected_version,
                                     str(model_config.get("expected_executable_sha256") or "").strip())
    source_home = _source_codex_home(model_config); auth = source_home / "auth.json"
    auth_kind = _auth_kind(auth)
    checks = {
        "executable": bool(provenance.get("valid")), "model_pin": model == "gpt-5.4",
        "reasoning_pin": reasoning == "medium", "provider_pin": provider == "openai",
        "billing_pin": billing == "subscription_oauth", "auth_exists": auth.is_file(),
        "subscription_oauth": auth_kind == "subscription_oauth",
    }
    return {"ok": all(checks.values()), "checks": checks, "provenance": provenance,
            "source_codex_home": str(source_home), "auth_kind": auth_kind,
            "model": model, "reasoning": reasoning, "provider": provider, "billing_mode": billing}


class CodexAdapter(BaseAdapter):
    name = "codex"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        preflight = codex_preflight(ctx.model_config)
        if not preflight["ok"]:
            return AdapterRunResult(ok=False, stderr="Codex preflight failed", metadata={"preflight": preflight})
        model = preflight["model"]; reasoning = preflight["reasoning"]
        provider = preflight["provider"]; billing = preflight["billing_mode"]
        source_home = Path(preflight["source_codex_home"]); source_auth = source_home / "auth.json"
        codex_home = ctx.sandbox / ".codex"; codex_home.mkdir(parents=True, exist_ok=True)
        staged_auth = _stage_auth(source_auth, codex_home)
        state_file = codex_home / "harnessbench-state.json"; state = _read_state(state_file)
        round_number = int(state.get("rounds", 0) or 0) + 1
        prior_session = str(state.get("session_id") or "").strip()
        extra_args = [str(x) for x in (ctx.model_config.get("extra_args") or [])]
        overrides = [str(x) for x in (ctx.model_config.get("config_overrides") or [])]
        conflicts = []
        for value in overrides:
            key = value.split("=", 1)[0].strip()
            if key in _RESERVED_CONFIG_KEYS: conflicts.append(value)
        reserved_flags = {"-m", "--model", "-c", "--config", "--profile", "-p", "--ephemeral", "--ignore-user-config"}
        conflicts.extend(x for x in extra_args if x in reserved_flags or any(x.startswith(f + "=") for f in reserved_flags if f.startswith("--")))
        if conflicts:
            _remove_auth(staged_auth)
            return AdapterRunResult(ok=False, stderr=f"reserved Codex overrides are not allowed: {conflicts}", metadata={"preflight": preflight})
        executable = str(preflight["provenance"]["resolved"])
        common = ["--json", "--model", model, "--strict-config", "--ignore-user-config", "--ignore-rules",
                  "--config", f"model_reasoning_effort={json.dumps(reasoning)}"]
        last_message = ctx.sandbox / f"codex-round{round_number}.last-message.txt"
        if prior_session:
            cmd = [executable, "exec", "resume", *common, "--output-last-message", str(last_message),
                   prior_session, "-"]
        else:
            cmd = [executable, "exec", "--cd", str(ctx.workspace), "--skip-git-repo-check",
                   "--sandbox", str(ctx.model_config.get("sandbox") or "workspace-write"),
                   "--ask-for-approval", "never", *common, "--output-last-message", str(last_message), "-"]
        # Place non-reserved additions before stdin sentinel.
        cmd[-1:-1] = [part for value in overrides for part in ("--config", value)] + extra_args
        env, removed_secrets = _filtered_env(ctx.env)
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
            _remove_auth(staged_auth)
            return AdapterRunResult(ok=False, command=cmd, stderr=str(exc), metadata={"preflight": preflight})
        except BaseException:
            if proc is not None and proc.poll() is None: _terminate_process_group(proc, 1)
            _remove_auth(staged_auth); raise
        try:
            stdout = "".join(stdout_chunks); stderr = "".join(stderr_chunks); rows = _json_rows(stdout)
            native_session_id = _session_id_from_rows(rows) or prior_session
            session_file = _find_session_file(codex_home, native_session_id)
            facts = _native_session_facts(session_file)
            native_call_count = max(1, int(facts["token_events"]) - int(state.get("native_token_events", 0) or 0))
            trace = write_codex_events_as_proxy_trace(stdout_text=stdout, stdout_log_file=stdout_log,
                proxy_dir=ctx.sandbox / "usage-proxy", task_id=ctx.task.task_id, session_id=ctx.session_id,
                model_id=ctx.model_id, model=model, provider=provider, initial_prompt=ctx.prompt,
                call_count=native_call_count)
            provider_ok = facts["provider"] in {"openai", "openai-codex"}
            model_ok = bool(facts["models"]) and set(facts["models"]) == {model}
            billing_ok = _auth_kind(staged_auth) == billing
            reasoning_ok = not facts["reasoning_efforts"] or set(facts["reasoning_efforts"]) == {reasoning}
            version_ok = not facts["cli_version"] or facts["cli_version"] in {"0.139.0", EXPECTED_CODEX_VERSION}
            ok = (returncode == 0 and not timed_out and bool(native_session_id) and session_file is not None
                  and trace["turn_completed"] and not trace["failure_event_seen"]
                  and trace["final_assistant_seen"] and trace["usage_available"]
                  and provider_ok and model_ok and reasoning_ok and billing_ok and version_ok)
            if native_session_id:
                tmp = state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps({"session_id": native_session_id, "rounds": round_number, "native_token_events": int(facts["token_events"])}, indent=2) + "\n", encoding="utf-8")
                tmp.replace(state_file)
            auth_synced = False
            if bool(ctx.model_config.get("sync_refreshed_auth", True)):
                auth_synced = _sync_staged_auth(source_auth, staged_auth)
            return AdapterRunResult(ok=ok, command=cmd, stdout=stdout, stderr=stderr, metadata={
                "returncode": returncode, "timed_out": timed_out, "preflight": preflight,
                "executable_provenance": preflight["provenance"], "provider": provider, "model": model,
                "model_reasoning_effort": reasoning, "billing_mode": billing, "provider_ok": provider_ok,
                "model_ok": model_ok, "reasoning_ok": reasoning_ok, "billing_ok": billing_ok, "native_version_ok": version_ok,
                "codex_home": str(codex_home), "state_dir": str(codex_home),
                "native_session_id": native_session_id, "codex_session_file": str(session_file or ""),
                "native_session_facts": facts, "native_call_count": native_call_count, "round_number": round_number, "resumed": bool(prior_session),
                "stdout_log_file": str(stdout_log), "stderr_log_file": str(stderr_log),
                "last_message_file": str(last_message), "synthetic_proxy_trace": trace,
                "filtered_host_secret_names": removed_secrets, "auth_synced": auth_synced,
                "staged_auth_removed": True, "credentials_retained_in_sandbox": False,
                "workspace": str(ctx.workspace)})
        finally:
            _remove_auth(staged_auth)
