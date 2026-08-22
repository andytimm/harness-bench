from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from harnessbench.adapters.hermes import HermesAgentAdapter, _minimal_auth
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_usage_summary


class HermesAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sandbox = self.root / "sandbox"
        self.workspace = self.sandbox / "workspace"
        self.workspace.mkdir(parents=True)
        self.prompt_file = self.sandbox / "prompt.txt"
        self.prompt_file.write_text("prompt", encoding="utf-8")
        self.source_auth = self.root / "auth.json"
        self.source_auth.write_text(json.dumps({
            "version": 3, "active_provider": "nous",
            "providers": {
                "nous": {"access_token": "must-not-leak"},
                "openai-codex": {"tokens": {"access_token": "access", "refresh_token": "refresh"}},
            },
            "credential_pool": {
                "nous": [{"access_token": "must-not-leak"}],
                "openai-codex": [{"access_token": "access", "refresh_token": "refresh"}],
            },
        }), encoding="utf-8")
        self.command = self.root / "fake-hermes"
        script = r'''#!/usr/bin/env python
import json, os, pathlib, sqlite3, sys, time
args = sys.argv[1:]
if "--version" in args:
    print("Hermes Agent v0.20.1")
    raise SystemExit(0)
def value(flag): return args[args.index(flag) + 1]
home = pathlib.Path(os.environ["HERMES_HOME"])
auth = json.loads((home / "auth.json").read_text())
assert set(auth["providers"]) == {"openai-codex"}
assert set(auth.get("credential_pool", {})) <= {"openai-codex"}
assert os.environ["HOME"] == str(home.parent)
assert "--safe-mode" in args and "--ignore-rules" in args and "-Q" in args
assert value("--provider") == "openai-codex"
assert value("--model") == "gpt-5.4"
assert value("--reasoning") == "medium"
prompt = value("-q")
if prompt == "SLEEP": time.sleep(30)
if prompt == "FAIL":
    print("provider failure", file=sys.stderr)
    raise SystemExit(2)
sid = value("--resume") if "--resume" in args else "20260815_120000_deadbeef"
db = sqlite3.connect(home / "state.db")
db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT, billing_mode TEXT, end_reason TEXT, message_count INTEGER, tool_call_count INTEGER, api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, started_at REAL)")
db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, finish_reason TEXT, timestamp REAL)")
db.execute("CREATE TABLE IF NOT EXISTS session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, billing_mode TEXT, task TEXT, api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, first_seen REAL, UNIQUE(session_id, task))")
db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid,"gpt-5.4","openai-codex","subscription_included","cli_close",2,0,1,12,3,2,0,1,0.0,0.0,"included",1.0))
db.execute("INSERT OR IGNORE INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid,"gpt-5.4","openai-codex","","title_generation",1,4,1,0,0,0,0.0,0.0,1.0))
next_ts = db.execute("SELECT COALESCE(MAX(timestamp), 0) + 1 FROM messages WHERE session_id = ?", (sid,)).fetchone()[0]
db.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES (?, 'user', ?, ?)", (sid,prompt,next_ts))
db.execute("INSERT INTO messages (session_id,role,content,finish_reason,timestamp) VALUES (?, 'assistant', 'done', 'stop', ?)", (sid,next_ts + 1))
db.commit(); db.close()
print("done")
print("session_id: " + sid, file=sys.stderr)
'''
        script = script.replace("#!/usr/bin/env python", f"#!{sys.executable}")
        self.command.write_text(script, encoding="utf-8")
        self.command.chmod(self.command.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def context(self, prompt: str = "DO", timeout: int = 5) -> AdapterRunContext:
        return AdapterRunContext(
            task=TaskSpec(task_id="001-file", title="test"), workspace=self.workspace,
            sandbox=self.sandbox, prompt=prompt, prompt_file=self.prompt_file,
            session_id="bench-id", timeout_sec=timeout, env={},
            model_id="hermes-gpt-5.4-medium", model_config={
                "command": str(self.command), "provider": "openai-codex", "model": "gpt-5.4",
                "reasoning": "medium", "user_auth": str(self.source_auth), "timeout_grace_sec": .1,
            }, mode="live",
        )

    def test_filters_auth_isolates_config_captures_native_usage_and_resumes(self) -> None:
        filtered = _minimal_auth(self.source_auth, "openai-codex")
        self.assertEqual(set(filtered["providers"]), {"openai-codex"})
        self.assertNotIn("must-not-leak", json.dumps(filtered))
        first = HermesAgentAdapter().run(self.context())
        self.assertTrue(first.ok, first.stderr)
        self.assertFalse((self.sandbox / ".hermes" / "auth.json").exists())
        self.assertEqual(first.metadata["hermes_session_id"], "20260815_120000_deadbeef")
        usage = _collect_usage_summary(first, "bench-id")
        self.assertEqual(usage["providers"], ["openai-codex"])
        self.assertEqual(usage["models"], ["gpt-5.4"])
        self.assertEqual(usage["billing_mode"], "subscription_included")
        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["auxiliary_usage_rows"], 1)
        self.assertEqual(usage["total_tokens"], 22)
        trace = first.metadata["synthetic_trace"]
        self.assertEqual(trace["response_count"], 1)
        response_files = sorted((self.sandbox / "usage-proxy" / "responses").glob("hermes-*.json"))
        self.assertEqual(len(response_files), 1)
        response = json.loads(response_files[0].read_text())
        self.assertEqual(json.loads(response["request_body"])["messages"][0]["role"], "user")
        self.assertFalse((self.sandbox / "usage-proxy" / "requests.jsonl").exists())
        second = HermesAgentAdapter().run(self.context())
        self.assertTrue(second.ok, second.stderr)
        self.assertIn("--resume", second.command)
        self.assertEqual(second.metadata["resume_method"], "resume")
        self.assertEqual(second.metadata["synthetic_trace"]["response_count"], 1)
        self.assertEqual(
            len(list((self.sandbox / "usage-proxy" / "responses").glob("hermes-*.json"))), 2
        )
        config = (self.sandbox / ".hermes" / "config.yaml").read_text()
        self.assertNotIn("must-not-leak", config)

    def test_nonzero_exit_is_failure_and_removes_auth(self) -> None:
        result = HermesAgentAdapter().run(self.context("FAIL"))
        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["returncode"], 2)
        self.assertFalse((self.sandbox / ".hermes" / "auth.json").exists())

    def test_timeout_kills_process_group_and_removes_auth(self) -> None:
        result = HermesAgentAdapter().run(self.context("SLEEP", timeout=1))
        self.assertFalse(result.ok)
        self.assertTrue(result.metadata["timed_out"])
        self.assertFalse((self.sandbox / ".hermes" / "auth.json").exists())


if __name__ == "__main__":
    unittest.main()
