from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from harnessbench.adapters.prime_agent import PrimeAgentAdapter, write_prime_events_as_proxy_trace
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_proxy_usage_summary


class PrimeAgentTraceTests(unittest.TestCase):
    def test_native_events_become_proxy_trace_without_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout_log = root / "raw.jsonl"
            events = [
                {"type": "session", "version": 3, "id": "session-1", "cwd": str(root)},
                {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "do it"}]}},
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "toolCall", "id": "call-1", "name": "bash", "arguments": {"command": "pwd"}}],
                        "provider": "openai-codex",
                        "model": "gpt-5.4",
                        "usage": {"input": 10, "output": 3, "cacheRead": 4, "cacheWrite": 0, "totalTokens": 13},
                        "stopReason": "toolUse",
                    },
                },
                {"type": "tool_execution_end", "toolCallId": "call-1", "toolName": "bash", "result": {"content": [{"type": "text", "text": "/work"}]}, "isError": False},
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                        "provider": "openai-codex",
                        "model": "gpt-5.4",
                        "usage": {"input": 20, "output": 2, "cacheRead": 5, "cacheWrite": 1, "totalTokens": 22},
                        "stopReason": "stop",
                    },
                },
                {"type": "turn_end", "message": {"role": "assistant", "usage": {"totalTokens": 22}}},
                {"type": "agent_end", "messages": []},
            ]
            stdout_text = "\n".join(json.dumps(event) for event in events) + "\n"
            stdout_log.write_text(stdout_text, encoding="utf-8")
            result = write_prime_events_as_proxy_trace(
                stdout_text=stdout_text,
                stdout_log_file=stdout_log,
                proxy_dir=root / "usage-proxy",
                task_id="001-file",
                session_id="bench-session",
                model_id="prime-agent-gpt-5.4-medium",
                initial_prompt="do it",
            )

            self.assertEqual(result["response_count"], 2)
            self.assertEqual(result["final_stop_reason"], "stop")
            usage = _collect_proxy_usage_summary(root / "usage-proxy" / "requests.jsonl", "bench-session")
            self.assertEqual(usage["request_count"], 2)
            self.assertEqual(usage["input_tokens"], 30)
            self.assertEqual(usage["output_tokens"], 5)
            self.assertEqual(usage["cache_read_tokens"], 9)
            self.assertEqual(usage["cache_write_tokens"], 1)
            self.assertEqual(usage["total_tokens"], 35)
            self.assertEqual(usage["models"], ["gpt-5.4"])

            first = json.loads((root / "usage-proxy" / "responses" / "prime-agent-0001.json").read_text())
            tool_call = first["response_json"]["choices"][0]["message"]["tool_calls"][0]
            self.assertEqual(tool_call["function"]["name"], "bash")
            self.assertEqual(json.loads(tool_call["function"]["arguments"]), {"command": "pwd"})


class PrimeAgentAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "sandbox" / "workspace"
        self.workspace.mkdir(parents=True)
        (self.workspace / "in").mkdir()
        (self.workspace / "out").mkdir()
        self.prompt_file = self.root / "sandbox" / "prompt.txt"
        self.prompt_file.write_text("test prompt", encoding="utf-8")
        self.source_agent_dir = self.root / "source-agent"
        self.source_agent_dir.mkdir()
        (self.source_agent_dir / "auth.json").write_text('{"openai-codex":{"type":"oauth"}}', encoding="utf-8")
        self.fake_command = self.root / "fake-prime-agent"
        self.fake_command.write_text(
            textwrap.dedent(
                f'''\
                #!{sys.executable}
                import json
                import pathlib
                import sys
                import time

                args = sys.argv[1:]
                def value(flag):
                    return args[args.index(flag) + 1]
                prompt = args[args.index("--") + 1]
                if prompt == "SLEEP":
                    time.sleep(30)
                session_dir = pathlib.Path(value("--session-dir"))
                session_dir.mkdir(parents=True, exist_ok=True)
                if "--resume" in args:
                    session_file = pathlib.Path(value("--resume"))
                    session_id = session_file.stem
                else:
                    session_id = "fake-session"
                    # Daemon-owned Prime sessions may use an active-agent ID as the
                    # filename while retaining the actual session ID in the header.
                    session_file = session_dir / "daemon-active-id.jsonl"
                header = {{"type":"session", "version":3, "id":session_id, "timestamp":"2026-01-01T00:00:00Z", "cwd":value("--cwd")}}
                message = {{
                    "role":"assistant",
                    "content":[{{"type":"text", "text":"done"}}],
                    "provider":value("--provider"),
                    "model":value("--model"),
                    "usage":{{"input":11,"output":2,"cacheRead":3,"cacheWrite":0,"totalTokens":13}},
                    "stopReason":"error" if prompt == "ERROR" else "stop"
                }}
                with session_file.open("a", encoding="utf-8") as handle:
                    if session_file.stat().st_size == 0:
                        handle.write(json.dumps(header) + "\\n")
                    handle.write(json.dumps({{"type":"message", "id":"m", "parentId":None, "timestamp":"2026-01-01T00:00:00Z", "message":message}}) + "\\n")
                print(json.dumps(header), flush=True)
                print(json.dumps({{"type":"message_end", "message":message}}), flush=True)
                '''
            ),
            encoding="utf-8",
        )
        self.fake_command.chmod(self.fake_command.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def context(self, prompt: str = "test prompt", timeout: float = 5) -> AdapterRunContext:
        return AdapterRunContext(
            task=TaskSpec(task_id="001-file", title="test"),
            workspace=self.workspace,
            sandbox=self.root / "sandbox",
            prompt=prompt,
            prompt_file=self.prompt_file,
            session_id="bench-session",
            timeout_sec=timeout,
            env={"HARNESSBENCH_LLM_PROXY_URL": "http://127.0.0.1:1"},
            model_id="prime-agent-gpt-5.4-medium",
            model_config={
                "command": str(self.fake_command),
                "user_agent_dir": str(self.source_agent_dir),
                "provider": "openai-codex",
                "model": "gpt-5.4",
                "thinking": "medium",
                "timeout_grace_sec": 0.1,
            },
            mode="live",
        )

    def test_run_is_isolated_and_second_round_resumes(self) -> None:
        adapter = PrimeAgentAdapter()
        first = adapter.run(self.context("first"))
        second = adapter.run(self.context("second"))

        self.assertTrue(first.ok, first.stderr)
        self.assertTrue(second.ok, second.stderr)
        self.assertFalse(first.metadata["resumed"])
        self.assertTrue(second.metadata["resumed"])
        self.assertEqual(first.metadata["session_id"], "fake-session")
        self.assertIn("--resume", second.command)
        resume_path = Path(second.command[second.command.index("--resume") + 1])
        self.assertTrue(resume_path.is_file())
        self.assertEqual(second.command[-2:], ["--", "second"])
        self.assertEqual(second.command[second.command.index("--thinking") + 1], "medium")
        self.assertEqual(second.command[second.command.index("--model") + 1], "gpt-5.4")
        isolated_auth = self.root / "sandbox" / ".prime" / "agent" / "auth.json"
        self.assertFalse(isolated_auth.exists())
        self.assertFalse(second.metadata["credentials_retained_in_sandbox"])
        self.assertTrue(Path(second.metadata["stdout_log_file"]).is_file())

        usage = _collect_proxy_usage_summary(self.root / "sandbox" / "usage-proxy" / "requests.jsonl", "bench-session")
        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["total_tokens"], 26)

    def test_json_exit_zero_with_error_stop_reason_is_failure(self) -> None:
        result = PrimeAgentAdapter().run(self.context("ERROR"))
        self.assertEqual(result.metadata["returncode"], 0)
        self.assertEqual(result.metadata["final_stop_reason"], "error")
        self.assertFalse(result.ok)

    @unittest.skipUnless(os.name == "posix", "process-group timeout behavior is POSIX-specific")
    def test_timeout_returns_failed_result_and_preserves_logs(self) -> None:
        result = PrimeAgentAdapter().run(self.context("SLEEP", timeout=0.1))
        self.assertFalse(result.ok)
        self.assertTrue(result.metadata["timed_out"])
        self.assertTrue(Path(result.metadata["stdout_log_file"]).is_file())
        self.assertTrue(Path(result.metadata["stderr_log_file"]).is_file())


if __name__ == "__main__":
    unittest.main()
