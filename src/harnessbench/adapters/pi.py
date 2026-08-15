from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, TextIO

from harnessbench.adapters.base import BaseAdapter
from harnessbench.models import AdapterRunContext, AdapterRunResult


_SUCCESS_STOP_REASONS = {"stop"}


@lru_cache(maxsize=16)
def _command_version(command: str) -> str:
    try:
        completed = subprocess.run(
            [command, "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if completed.returncode == 0 and output else ""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_path(raw: str | Path) -> Path:
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = _project_root() / path
    return path.resolve()


def _stage_auth_state(source_agent_dir: Path, target_agent_dir: Path) -> list[str]:
    """Stage credentials/model definitions without loading user prompts or extensions."""
    staged: list[str] = []
    target_agent_dir.mkdir(parents=True, exist_ok=True)
    for name in ("auth.json",):
        source = source_agent_dir / name
        if not source.is_file():
            continue
        target = target_agent_dir / name
        if target.exists() or target.is_symlink():
            target.unlink()
        if name == "auth.json":
            try:
                # Share the normal OAuth store so token refresh remains locked and
                # durable, but remove the link before retaining the benchmark sandbox.
                target.symlink_to(source)
            except OSError:
                shutil.copy2(source, target)
                target.chmod(0o600)
        else:
            shutil.copy2(source, target)
        staged.append(name)
    return staged


def _remove_staged_credentials(target_agent_dir: Path) -> None:
    auth_path = target_agent_dir / "auth.json"
    try:
        if auth_path.exists() or auth_path.is_symlink():
            auth_path.unlink()
    except OSError:
        pass


def _json_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _session_header(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("type") == "session" and row.get("id")), None)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part)


def _tool_calls(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {"toolCall", "tool_call"}:
            continue
        raw_args = block.get("arguments", block.get("args", {}))
        if isinstance(raw_args, str):
            arguments = raw_args
        else:
            arguments = json.dumps(raw_args, ensure_ascii=False)
        calls.append(
            {
                "id": str(block.get("id") or block.get("toolCallId") or ""),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or block.get("toolName") or ""),
                    "arguments": arguments,
                },
            }
        )
    return calls


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        text = _message_text(content)
        if text:
            return text
    try:
        return json.dumps(result, ensure_ascii=False)
    except TypeError:
        return str(result)


def _usage_values(message: dict[str, Any]) -> dict[str, Any]:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = int(usage.get("input", 0) or 0)
    output_tokens = int(usage.get("output", 0) or 0)
    cache_read = int(usage.get("cacheRead", 0) or 0)
    cache_write = int(usage.get("cacheWrite", 0) or 0)
    total = int(usage.get("totalTokens", 0) or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": total,
    }


def _next_response_number(responses_dir: Path) -> int:
    existing: list[int] = []
    for path in responses_dir.glob("pi-*.json"):
        try:
            existing.append(int(path.stem.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return max(existing, default=0) + 1


def write_pi_events_as_proxy_trace(
    *,
    stdout_text: str,
    stdout_log_file: Path,
    proxy_dir: Path,
    task_id: str,
    session_id: str,
    model_id: str,
    initial_prompt: str,
) -> dict[str, Any]:
    """Translate finalized native events into Harness-Bench's proxy record shape."""
    responses_dir = proxy_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    requests_log = proxy_dir / "requests.jsonl"
    response_number = _next_response_number(responses_dir)
    usage_rows: list[str] = []
    conversation: list[dict[str, Any]] = [{"role": "user", "content": initial_prompt}]
    response_count = 0
    last_assistant: dict[str, Any] | None = None

    for row in _json_rows(stdout_text):
        row_type = str(row.get("type") or "")
        if row_type == "message_end":
            message = row.get("message")
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role == "user":
                text = _message_text(message.get("content"))
                if text and not (len(conversation) == 1 and text == initial_prompt):
                    conversation.append({"role": "user", "content": text})
                continue
            if role != "assistant":
                continue

            text = _message_text(message.get("content"))
            tool_calls = _tool_calls(message.get("content"))
            if not text and not tool_calls:
                last_assistant = message
                continue
            response_path = responses_dir / f"pi-{response_number:04d}.json"
            response_model = str(message.get("model") or model_id)
            provider = str(message.get("provider") or "pi-native")
            response_message: dict[str, Any] = {
                "role": "assistant",
                "content": text,
            }
            if tool_calls:
                response_message["tool_calls"] = tool_calls
            raw_record = {
                "task_id": task_id,
                "session_id": session_id,
                "model_id": model_id,
                "framework": "pi",
                "provider": provider,
                "request_body": json.dumps({"messages": conversation}, ensure_ascii=False),
                "response_json": {
                    "model": response_model,
                    "choices": [{"message": response_message}],
                },
                "source_stdout_log_file": str(stdout_log_file),
            }
            response_path.write_text(json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8")
            usage_row = {
                "task_id": task_id,
                "session_id": session_id,
                "model_id": model_id,
                "framework": "pi",
                "provider": provider,
                "raw_response_file": str(response_path),
                "response_model": response_model,
                **_usage_values(message),
            }
            usage_rows.append(json.dumps(usage_row, ensure_ascii=False))
            assistant_context: dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_context["tool_calls"] = tool_calls
            conversation.append(assistant_context)
            response_number += 1
            response_count += 1
            last_assistant = message
            continue

        if row_type == "tool_execution_end":
            tool_call_id = str(row.get("toolCallId") or "")
            tool_message: dict[str, Any] = {
                "role": "tool",
                "content": _result_text(row.get("result")),
            }
            if tool_call_id:
                tool_message["tool_call_id"] = tool_call_id
            conversation.append(tool_message)

    if usage_rows:
        with requests_log.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(usage_rows) + "\n")

    final_stop_reason = str((last_assistant or {}).get("stopReason") or "")
    return {
        "proxy_dir": str(proxy_dir),
        "requests_log": str(requests_log),
        "response_count": response_count,
        "final_stop_reason": final_stop_reason,
        "final_assistant_seen": last_assistant is not None,
    }


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_session_file(session_dir: Path, session_id: str) -> Path | None:
    """Find a transcript by its header ID; daemon-owned filenames may use a different ID."""
    if not session_id or not session_dir.is_dir():
        return None
    direct = session_dir / f"{session_id}.jsonl"
    candidates = [direct] if direct.is_file() else []
    candidates.extend(
        path
        for path in sorted(session_dir.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
        if path != direct
    )
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                first_line = handle.readline()
            header = json.loads(first_line)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(header, dict) and header.get("type") == "session" and str(header.get("id") or "") == session_id:
            return path
    return None


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


class PiAdapter(BaseAdapter):
    name = "pi"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "pi")
        provider = str(ctx.model_config.get("provider") or "openai-codex").strip()
        model = str(ctx.model_config.get("model") or "gpt-5.4").strip()
        thinking = str(ctx.model_config.get("thinking") or "medium").strip()
        source_agent_dir = _resolve_path(str(ctx.model_config.get("user_agent_dir") or "~/.pi/agent"))
        source_auth = source_agent_dir / "auth.json"
        if not source_auth.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing Pi auth store: {source_auth}")
        try:
            auth_data = json.loads(source_auth.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return AdapterRunResult(ok=False, stderr=f"invalid Pi auth store {source_auth}: {exc}")
        if not isinstance(auth_data, dict) or provider not in auth_data:
            return AdapterRunResult(
                ok=False,
                stderr=f"Pi provider {provider!r} is not authenticated in {source_auth}",
            )
        pi_version = _command_version(command)
        if not pi_version:
            return AdapterRunResult(ok=False, stderr=f"Pi command is unavailable or invalid: {command}")

        isolated_agent_dir = ctx.sandbox / ".pi" / "agent"
        staged_state = _stage_auth_state(source_agent_dir, isolated_agent_dir)
        session_dir = ctx.sandbox / "pi-sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        state_file = ctx.sandbox / "pi-adapter-state.json"
        state = _read_state(state_file)
        round_number = int(state.get("rounds", 0) or 0) + 1
        prior_session_file = str(state.get("session_file") or "").strip()

        cmd = [
            command,
            "--mode",
            "json",
            "--print",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
        ]
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])
        if thinking:
            cmd.extend(["--thinking", thinking])
        cmd.extend(["--session-dir", str(session_dir)])
        if prior_session_file:
            cmd.extend(["--session", prior_session_file])
        for item in ctx.model_config.get("extra_args") or []:
            cmd.append(str(item))
        cmd.append(ctx.prompt)

        env = os.environ.copy()
        env.update(ctx.env)
        for key in list(env):
            if key.startswith("PI_INTERNAL_"):
                env.pop(key, None)
        env.pop("FORCE_COLOR", None)
        env["NO_COLOR"] = "1"
        env["PI_TELEMETRY"] = "0"
        env["PI_CODING_AGENT_DIR"] = str(isolated_agent_dir)
        env["PI_CODING_AGENT_SESSION_DIR"] = str(session_dir)
        env.setdefault("PI_OFFLINE", "1")
        env.setdefault("PI_SKIP_VERSION_CHECK", "1")
        env["WORKSPACE"] = str(ctx.workspace)
        env["HARNESSBENCH_TASK_ID"] = ctx.task.task_id
        env["HARNESSBENCH_WORKSPACE"] = str(ctx.workspace)
        env["HARNESSBENCH_SANDBOX"] = str(ctx.sandbox)
        env["HARNESSBENCH_SESSION_ID"] = ctx.session_id
        env["HARNESSBENCH_PROMPT_FILE"] = str(ctx.prompt_file)
        env["HARNESSBENCH_MODEL_ID"] = ctx.model_id

        stdout_log_file = ctx.sandbox / f"pi-round{round_number}.stdout.jsonl"
        stderr_log_file = ctx.sandbox / f"pi-round{round_number}.stderr.log"
        stream_to_console = bool(ctx.model_config.get("stream_to_console", False))
        print(f"[harnessbench:pi] stdout_log={stdout_log_file}", flush=True)
        print(f"[harnessbench:pi] stderr_log={stderr_log_file}", flush=True)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ctx.workspace),
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=1,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            _remove_staged_credentials(isolated_agent_dir)
            return AdapterRunResult(
                ok=False,
                command=cmd,
                stderr=str(exc),
                metadata={"returncode": None, "timed_out": False},
            )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def reader(pipe: TextIO | None, sink: list[str], log_file: Path, mirror: TextIO | None) -> None:
            try:
                assert pipe is not None
                with log_file.open("w", encoding="utf-8", buffering=1) as handle:
                    for line in iter(pipe.readline, ""):
                        sink.append(line)
                        handle.write(line)
                        if mirror is not None:
                            mirror.write(line)
                            mirror.flush()
            finally:
                if pipe is not None:
                    pipe.close()

        stdout_thread = threading.Thread(
            target=reader,
            args=(proc.stdout, stdout_chunks, stdout_log_file, sys.stdout if stream_to_console else None),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=reader,
            args=(proc.stderr, stderr_chunks, stderr_log_file, sys.stderr if stream_to_console else None),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        timed_out = False
        try:
            returncode = proc.wait(timeout=ctx.timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            grace_sec = float(ctx.model_config.get("timeout_grace_sec", 5) or 5)
            returncode = _terminate_process_group(proc, grace_sec)

        stdout_thread.join(timeout=2 if timed_out else None)
        stderr_thread.join(timeout=2 if timed_out else None)
        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)
        rows = _json_rows(stdout_text)
        header = _session_header(rows)
        native_session_id = str((header or {}).get("id") or state.get("session_id") or "")
        session_file = _find_session_file(session_dir, native_session_id)
        if session_file is None and prior_session_file and Path(prior_session_file).is_file():
            session_file = Path(prior_session_file)

        synthetic_trace = write_pi_events_as_proxy_trace(
            stdout_text=stdout_text,
            stdout_log_file=stdout_log_file,
            proxy_dir=ctx.sandbox / "usage-proxy",
            task_id=ctx.task.task_id,
            session_id=ctx.session_id,
            model_id=ctx.model_id,
            initial_prompt=ctx.prompt,
        )
        final_stop_reason = str(synthetic_trace.get("final_stop_reason") or "")
        final_assistant_seen = bool(synthetic_trace.get("final_assistant_seen"))
        agent_end_seen = any(row.get("type") == "agent_end" for row in rows)
        ok = (
            returncode == 0
            and not timed_out
            and final_assistant_seen
            and agent_end_seen
            and final_stop_reason in _SUCCESS_STOP_REASONS
        )

        persisted_state = {
            "rounds": round_number,
            "session_id": native_session_id,
            "session_file": str(session_file) if session_file else prior_session_file,
        }
        state_file.write_text(json.dumps(persisted_state, ensure_ascii=False, indent=2), encoding="utf-8")

        artifact_dir = (
            ctx.sandbox / "session-artifacts" / native_session_id
            if native_session_id
            else ctx.sandbox / "session-artifacts"
        )
        _remove_staged_credentials(isolated_agent_dir)
        return AdapterRunResult(
            ok=ok,
            command=cmd,
            stdout=stdout_text,
            stderr=stderr_text,
            metadata={
                "returncode": returncode,
                "timed_out": timed_out,
                "pi_version": pi_version,
                "provider": provider,
                "model": model,
                "thinking": thinking,
                "source_agent_dir": str(source_agent_dir),
                "isolated_agent_dir": str(isolated_agent_dir),
                "staged_state_files": staged_state,
                "credentials_retained_in_sandbox": False,
                "session_dir": str(session_dir),
                "state_dir": str(session_dir),
                "session_id": native_session_id,
                "session_file": str(session_file) if session_file else "",
                "session_artifact_dir": str(artifact_dir),
                "round_number": round_number,
                "resumed": bool(prior_session_file),
                "stdout_log_file": str(stdout_log_file),
                "stderr_log_file": str(stderr_log_file),
                "synthetic_proxy_trace": synthetic_trace,
                "final_stop_reason": final_stop_reason,
                "final_assistant_seen": final_assistant_seen,
                "agent_end_seen": agent_end_seen,
                "workspace": str(ctx.workspace),
            },
        )
