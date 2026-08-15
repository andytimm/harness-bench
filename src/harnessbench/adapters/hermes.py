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
    except (OSError, ValueError, json.JSONDecodeError):
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
            "message_count, api_call_count FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        assistant_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'assistant'", (session_id,)
        ).fetchone()[0]
        connection.close()
    except sqlite3.Error:
        return {}
    result = dict(row) if row is not None else {}
    result["assistant_message_count"] = int(assistant_count or 0)
    return result


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
        for arg in ctx.model_config.get("extra_args") or []:
            cmd.append(str(arg))
        cmd.extend(["-q", ctx.prompt])

        env = os.environ.copy()
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
                returncode = _terminate_process_group(
                    proc, float(ctx.model_config.get("timeout_grace_sec", 5) or 5)
                )
                stdout, stderr = proc.communicate()
        except OSError as exc:
            _remove_auth(staged_auth)
            return AdapterRunResult(ok=False, command=cmd, stderr=str(exc))

        auth_synced = _sync_staged_auth(source_auth, staged_auth, provider)
        _remove_auth(staged_auth)
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")
        parsed_session = _SESSION_ID_RE.search(stdout + "\n" + stderr)
        session_id = parsed_session.group(1) if parsed_session else prior_session
        native = _session_record(hermes_home / "state.db", session_id)
        provider_ok = native.get("billing_provider") == provider
        model_ok = native.get("model") == model
        completed = bool(native.get("end_reason")) and int(native.get("assistant_message_count", 0)) > 0
        ok = not timed_out and returncode == 0 and bool(session_id) and completed and provider_ok and model_ok

        if session_id:
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"session_id": session_id, "rounds": round_number}, indent=2) + "\n",
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
                "resume_method": "resume" if prior_session else "none",
                "stdout_log_file": str(stdout_log), "stderr_log_file": str(stderr_log),
                "native_session": native, "auth_synced": auth_synced,
                "staged_auth_removed": not staged_auth.exists(),
            },
        )
