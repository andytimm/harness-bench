from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import tempfile
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult

_SESSION_ID_RE = re.compile(r"(?:session_id:|Session:)\s*([0-9]{8}_[0-9]{6}_[0-9a-f]+)", re.IGNORECASE)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_path(raw: str | Path) -> Path:
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = _project_root() / path
    return path.resolve()


@lru_cache(maxsize=16)
def _command_version(command: str) -> str:
    try:
        completed = subprocess.run(
            [command, "--version"], text=True, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if completed.returncode == 0 and output else ""


def _minimal_auth(source: Path, provider: str) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    providers = data.get("providers") if isinstance(data, dict) else None
    provider_state = providers.get(provider) if isinstance(providers, dict) else None
    pools = data.get("credential_pool") if isinstance(data, dict) else None
    pool = pools.get(provider) if isinstance(pools, dict) else None
    if not isinstance(provider_state, dict) or not provider_state:
        raise ValueError(f"provider {provider!r} is not authenticated")
    staged: dict[str, Any] = {
        "version": data.get("version", 1),
        "providers": {provider: provider_state},
        "active_provider": provider,
    }
    if isinstance(pool, list) and pool:
        staged["credential_pool"] = {provider: pool}
    return staged


def _stage_auth(source: Path, hermes_home: Path, provider: str) -> Path:
    target = hermes_home / "auth.json"
    target.write_text(json.dumps(_minimal_auth(source, provider), indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)
    return target


def _sync_staged_auth(source: Path, staged: Path, provider: str) -> bool:
    """Atomically copy back only rotated credentials for the selected provider."""
    if not staged.is_file():
        return False
    try:
        staged_data = json.loads(staged.read_text(encoding="utf-8"))
        staged_provider = (staged_data.get("providers") or {}).get(provider)
        staged_pool = (staged_data.get("credential_pool") or {}).get(provider)
        if not isinstance(staged_provider, dict):
            return False
        lock_path = source.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = json.loads(source.read_text(encoding="utf-8"))
            current.setdefault("providers", {})[provider] = staged_provider
            if isinstance(staged_pool, list):
                current.setdefault("credential_pool", {})[provider] = staged_pool
            fd, temporary_name = tempfile.mkstemp(prefix=source.name + ".tmp.", dir=source.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(current, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, source)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return True
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _remove_auth(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _write_minimal_config(path: Path, provider: str, model: str, reasoning: str) -> None:
    # safe-mode ignores user configuration, but an explicit minimal file makes the
    # isolated home self-describing and prevents accidental fallback if flags change.
    path.write_text(
        "model:\n"
        f"  default: {json.dumps(model)}\n"
        f"  provider: {json.dumps(provider)}\n"
        "agent:\n"
        f"  reasoning_effort: {json.dumps(reasoning)}\n"
        "memory:\n"
        "  memory_enabled: false\n"
        "  user_profile_enabled: false\n"
        "skills:\n"
        "  external_dirs: []\n",
        encoding="utf-8",
    )


def _session_record(db_file: Path, session_id: str) -> dict[str, Any]:
    if not db_file.is_file() or not session_id:
        return {}
    try:
        connection = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, model, billing_provider, billing_mode, end_reason, "
            "message_count, tool_call_count, api_call_count, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        assistant_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'assistant'",
            (session_id,),
        ).fetchone()[0]
        connection.close()
    except sqlite3.Error:
        return {}
    result = dict(row) if row is not None else {}
    result["assistant_message_count"] = int(assistant_count or 0)
    return result


def _json_list(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _message_for_request(row: sqlite3.Row) -> dict[str, Any]:
    role = str(row["role"] or "")
    content: Any = row["content"] or ""
    if isinstance(content, str) and content.startswith("\x00json:"):
        try:
            content = json.loads(content[len("\x00json:"):])
        except json.JSONDecodeError:
            pass
    message: dict[str, Any] = {"role": role, "content": content}
    if role == "assistant":
        tool_calls = _json_list(row["tool_calls"])
        if tool_calls:
            message["tool_calls"] = tool_calls
    elif role == "tool":
        tool_call_id = str(row["tool_call_id"] or "")
        if tool_call_id:
            message["tool_call_id"] = tool_call_id
        tool_name = str(row["tool_name"] or "")
        if tool_name:
            message["name"] = tool_name
    return message


def write_hermes_session_as_proxy_trace(
    *,
    db_file: Path,
    native_session_id: str,
    proxy_dir: Path,
    task_id: str,
    harness_session_id: str,
    model_id: str,
    provider: str,
    after_message_id: int = 0,
) -> dict[str, Any]:
    """Retain task-local native messages in the proxy shape used for grading.

    The database is isolated per benchmark task, so all its sessions belong to
    the evaluated agent tree (including compression continuations/delegates).
    Hermes stores only aggregate usage, so no per-response token rows are
    fabricated; exact aggregate usage remains sourced from SQLite.
    """
    responses_dir = proxy_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        session_rows = connection.execute("SELECT * FROM sessions").fetchall()
        sessions = {str(row["id"]): dict(row) for row in session_rows}
        if native_session_id not in sessions:
            raise sqlite3.DatabaseError(f"Hermes session {native_session_id!r} is absent")
        prompts: dict[str, str] = {}
        try:
            prompts = {
                str(row["hash"]): str(row["prompt"])
                for row in connection.execute("SELECT hash, prompt FROM system_prompts")
            }
        except sqlite3.OperationalError:
            pass
        rows = connection.execute(
            "SELECT id, session_id, role, content, tool_call_id, tool_calls, "
            "tool_name, finish_reason, timestamp FROM messages ORDER BY id"
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        return {"available": False, "error": str(exc), "response_count": 0}

    existing_numbers: list[int] = []
    for path in responses_dir.glob("hermes-*.json"):
        try:
            existing_numbers.append(int(path.stem.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    response_number = max(existing_numbers, default=0) + 1
    conversations: dict[str, list[dict[str, Any]]] = {}
    for session_key, session in sessions.items():
        system_prompt = str(session.get("system_prompt") or "")
        if not system_prompt:
            system_prompt = prompts.get(str(session.get("system_prompt_hash") or ""), "")
        conversations[session_key] = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )

    response_count = 0
    last_message_id = int(after_message_id or 0)
    final_finish_reason = ""
    for row in rows:
        message_id = int(row["id"])
        session_key = str(row["session_id"])
        conversation = conversations.setdefault(session_key, [])
        message = _message_for_request(row)
        if message["role"] != "assistant":
            conversation.append(message)
            last_message_id = max(last_message_id, message_id)
            continue

        session = sessions.get(session_key, {})
        if message_id > after_message_id:
            response_path = responses_dir / f"hermes-{response_number:04d}.json"
            response_model = str(session.get("model") or model_id)
            response_provider = str(session.get("billing_provider") or provider)
            raw_record = {
                "task_id": task_id,
                "session_id": harness_session_id,
                "native_session_id": session_key,
                "native_parent_session_id": session.get("parent_session_id"),
                "native_session_source": session.get("source"),
                "model_id": model_id,
                "framework": "hermes",
                "provider": response_provider,
                "request_body": json.dumps({"messages": conversation}, ensure_ascii=False),
                "response_json": {
                    "model": response_model,
                    "choices": [{"message": message, "finish_reason": row["finish_reason"]}],
                },
                "source_database_file": str(db_file),
                "source_message_id": message_id,
            }
            response_path.write_text(
                json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            response_number += 1
            response_count += 1
        conversation.append(message)
        last_message_id = max(last_message_id, message_id)
        if session_key == native_session_id:
            final_finish_reason = str(row["finish_reason"] or final_finish_reason)

    return {
        "available": bool(rows),
        "proxy_dir": str(proxy_dir),
        "response_count": response_count,
        "last_message_id": last_message_id,
        "final_finish_reason": final_finish_reason,
        "native_message_count": len(rows),
        "native_session_count": len(sessions),
    }

def _terminate_process_group(proc: subprocess.Popen[str], grace_sec: float) -> int:
    if proc.poll() is not None:
        return int(proc.returncode or 0)
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        return proc.wait(timeout=max(0.1, grace_sec))
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        return proc.wait()


class HermesAgentAdapter(BaseAdapter):
    name = "hermes_agent"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "hermes")
        provider = str(ctx.model_config.get("provider") or "openai-codex").strip()
        model = str(ctx.model_config.get("model") or "gpt-5.4").strip()
        reasoning = str(ctx.model_config.get("reasoning") or "medium").strip()
        source_auth = _resolve_path(str(ctx.model_config.get("user_auth") or "~/.hermes/auth.json"))
        version = _command_version(command)
        if not version:
            return AdapterRunResult(ok=False, stderr=f"Hermes command is unavailable or invalid: {command}")
        if not source_auth.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing Hermes auth store: {source_auth}")
        extra_args = [str(arg) for arg in (ctx.model_config.get("extra_args") or [])]
        reserved_flags = {
            "-q", "--query", "--provider", "--model", "--reasoning", "--in",
            "--resume", "--no-restore-cwd", "--source", "--safe-mode",
            "--ignore-user-config", "--ignore-rules",
        }
        conflicts = [
            arg for arg in extra_args
            if arg in reserved_flags or any(arg.startswith(flag + "=") for flag in reserved_flags if flag.startswith("--"))
        ]
        if conflicts:
            return AdapterRunResult(ok=False, stderr=f"reserved Hermes extra_args are not allowed: {conflicts}")

        hermes_home = ctx.sandbox / ".hermes"
        hermes_home.mkdir(parents=True, exist_ok=True)
        config_file = hermes_home / "config.yaml"
        _write_minimal_config(config_file, provider, model, reasoning)
        try:
            staged_auth = _stage_auth(source_auth, hermes_home, provider)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return AdapterRunResult(ok=False, stderr=f"invalid Hermes auth store {source_auth}: {exc}")

        state_file = hermes_home / "harnessbench-state.json"
        state: dict[str, Any] = {}
        if state_file.is_file():
            try:
                loaded = json.loads(state_file.read_text(encoding="utf-8"))
                state = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
        round_number = int(state.get("rounds", 0) or 0) + 1
        prior_session = str(state.get("session_id") or "").strip()

        cmd = [
            command, "chat", "-Q", "--safe-mode", "--ignore-rules", "--source", "tool",
            "--provider", provider, "--model", model, "--reasoning", reasoning,
            "--in", str(ctx.workspace),
        ]
        if prior_session:
            cmd.extend(["--resume", prior_session, "--no-restore-cwd"])
        cmd.extend(extra_args)
        cmd.extend(["-q", ctx.prompt])

        env = os.environ.copy()
        # Preserve normal execution variables but do not expose unrelated host
        # credentials to an evaluated same-UID tool process. Task-provided
        # runtime variables are overlaid afterwards and remain available.
        secret_names = {
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
            "SSH_AUTH_SOCK", "GOOGLE_APPLICATION_CREDENTIALS", "DATABASE_URL",
        }
        for key in list(env):
            upper = key.upper()
            if (
                key in secret_names
                or upper.endswith(("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_SECRET"))
                or upper.startswith(("AWS_SECRET_", "AZURE_CLIENT_SECRET"))
            ):
                env.pop(key, None)
        env.update(ctx.env)
        env["HOME"] = str(ctx.sandbox)
        env["HERMES_HOME"] = str(hermes_home)
        env["HERMES_CONFIG"] = str(config_file)
        env["HERMES_CONFIG_PATH"] = str(config_file)
        env["NO_COLOR"] = "1"
        env.pop("FORCE_COLOR", None)
        env["WORKSPACE"] = str(ctx.workspace)
        env["HARNESSBENCH_WORKSPACE"] = str(ctx.workspace)
        env["HARNESSBENCH_SANDBOX"] = str(ctx.sandbox)
        env["HARNESSBENCH_TASK_ID"] = ctx.task.task_id

        stdout_log = ctx.sandbox / f"hermes-round{round_number}.stdout.log"
        stderr_log = ctx.sandbox / f"hermes-round{round_number}.stderr.log"
        timed_out = False
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ctx.workspace), text=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                start_new_session=(os.name == "posix"),
            )
            try:
                stdout, stderr = proc.communicate(timeout=ctx.timeout_sec)
                returncode = int(proc.returncode or 0)
            except subprocess.TimeoutExpired:
                timed_out = True
                grace_sec = float(ctx.model_config.get("timeout_grace_sec", 5) or 5)
                returncode = _terminate_process_group(proc, grace_sec)
                try:
                    stdout, stderr = proc.communicate(timeout=max(0.1, grace_sec))
                except subprocess.TimeoutExpired as exc:
                    stdout = str(exc.output or "")
                    stderr = str(exc.stderr or "") + "\noutput pipe did not close after process termination"
                    for stream in (proc.stdout, proc.stderr):
                        if stream is not None:
                            stream.close()
        except OSError as exc:
            _remove_auth(staged_auth)
            return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))
        except BaseException:
            if proc is not None and proc.poll() is None:
                try:
                    _terminate_process_group(proc, 1)
                except Exception:
                    pass
            _remove_auth(staged_auth)
            raise

        try:
            auth_synced = _sync_staged_auth(source_auth, staged_auth, provider)
        finally:
            _remove_auth(staged_auth)
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")
        parsed_session = _SESSION_ID_RE.search(stdout + "\n" + stderr)
        session_id = parsed_session.group(1) if parsed_session else prior_session
        database_file = hermes_home / "state.db"
        native = _session_record(database_file, session_id)
        synthetic_trace = write_hermes_session_as_proxy_trace(
            db_file=database_file,
            native_session_id=session_id,
            proxy_dir=ctx.sandbox / "usage-proxy",
            task_id=ctx.task.task_id,
            harness_session_id=ctx.session_id,
            model_id=model,
            provider=provider,
            after_message_id=int(state.get("last_proxy_message_id", 0) or 0),
        )
        provider_ok = native.get("billing_provider") == provider
        model_ok = native.get("model") == model
        billing_ok = native.get("billing_mode") == "subscription_included"
        completed = (
            int(native.get("assistant_message_count", 0)) > 0
            and int(synthetic_trace.get("response_count", 0)) > 0
            and synthetic_trace.get("final_finish_reason") == "stop"
        )
        ok = (
            not timed_out
            and returncode == 0
            and bool(session_id)
            and completed
            and provider_ok
            and model_ok
            and billing_ok
        )

        if session_id:
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "rounds": round_number,
                        "last_proxy_message_id": int(synthetic_trace.get("last_message_id", 0) or 0),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(state_file)

        return AdapterRunResult(
            ok=ok, command=cmd, stdout=stdout, stderr=stderr,
            metadata={
                "returncode": returncode, "timed_out": timed_out,
                "hermes_version": version, "provider": provider, "model": model,
                "reasoning": reasoning, "hermes_home": str(hermes_home),
                "hermes_config_path": str(config_file), "hermes_session_id": session_id,
                "usage_source": "hermes_sqlite",
                "resume_method": "resume" if prior_session else "none",
                "stdout_log_file": str(stdout_log), "stderr_log_file": str(stderr_log),
                "native_session": native, "synthetic_trace": synthetic_trace,
                "auth_synced": auth_synced,
                "staged_auth_removed": not staged_auth.exists(),
            },
        )
