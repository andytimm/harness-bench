from __future__ import annotations
import json, os, stat, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from harnessbench.adapters.codex import CodexAdapter, codex_preflight, write_codex_events_as_proxy_trace
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_proxy_usage_summary

class CodexTraceTests(unittest.TestCase):
 def test_turn_usage_retains_cache_without_double_counting(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); log=root/"stdout.jsonl"
   rows=[{"type":"thread.started","thread_id":"thread-1"},{"type":"turn.started"},{"type":"item.completed","item":{"id":"cmd-1","type":"command_execution","command":"pwd","aggregated_output":"/work","exit_code":0,"status":"completed"}},{"type":"item.completed","item":{"id":"msg-1","type":"agent_message","text":"done"}},{"type":"turn.completed","usage":{"input_tokens":20,"cached_input_tokens":8,"output_tokens":5,"total_tokens":25}}]
   text="\n".join(json.dumps(x) for x in rows)+"\n"; log.write_text(text)
   trace=write_codex_events_as_proxy_trace(stdout_text=text,stdout_log_file=log,proxy_dir=root/"usage-proxy",task_id="001-file",session_id="bench",model_id="codex-current",model="gpt-5.4",provider="openai",initial_prompt="do it")
   self.assertTrue(trace["turn_completed"]); self.assertTrue(trace["final_assistant_seen"])
   usage=_collect_proxy_usage_summary(root/"usage-proxy"/"requests.jsonl","bench")
   self.assertEqual(usage["request_count"],1); self.assertEqual(usage["cache_read_tokens"],8); self.assertEqual(usage["total_tokens"],25)

class CodexAdapterTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.sandbox=self.root/"sandbox"; self.workspace=self.sandbox/"workspace"
  self.workspace.mkdir(parents=True); (self.workspace/"in").mkdir(); (self.workspace/"out").mkdir(); self.prompt_file=self.sandbox/"prompt.txt"; self.prompt_file.write_text("prompt")
  self.source=self.root/"source"; self.source.mkdir(); (self.source/"auth.json").write_text(json.dumps({"OPENAI_API_KEY":None,"tokens":{"access_token":"secret","refresh_token":"refresh"}}))
  self.command=self.root/"codex"; self.command.write_text("#!"+sys.executable+"\n"+'import json, os, pathlib, sys, time\nargs=sys.argv[1:]\nif args == ["--version"]:\n    print("codex-cli 0.139.0"); raise SystemExit(0)\nhome=pathlib.Path(os.environ["CODEX_HOME"])\nassert (home/"auth.json").is_file()\nassert "OPENAI_API_KEY" not in os.environ and "TEST_PASSWORD" not in os.environ\nassert "--model" in args and args[args.index("--model")+1] == "gpt-5.4"\nassert \'model_reasoning_effort="medium"\' in args\nprompt=sys.stdin.read()\nif prompt == "SLEEP": time.sleep(30)\nstate=home/"harnessbench-state.json"\nsid=json.loads(state.read_text())["session_id"] if state.is_file() else "11111111-1111-1111-1111-111111111111"\nsession=home/"sessions"/"2026"/"01"/"rollout.jsonl"; session.parent.mkdir(parents=True,exist_ok=True)\nwith session.open("a") as f:\n f.write(json.dumps({"type":"session_meta","payload":{"id":sid,"model_provider":"openai","cli_version":"0.139.0"}})+"\\n")\n f.write(json.dumps({"type":"turn_context","payload":{"model":"gpt-5.4"}})+"\\n")\n f.write(json.dumps({"type":"event_msg","payload":{"type":"token_count"}})+"\\n")\nprint(json.dumps({"type":"thread.started","thread_id":sid}),flush=True)\nprint(json.dumps({"type":"turn.started"}),flush=True)\nif prompt == "FAIL":\n print(json.dumps({"type":"turn.failed","error":{"message":"bad"}}),flush=True); raise SystemExit(0)\nprint(json.dumps({"type":"item.completed","item":{"id":"m","type":"agent_message","text":"done"}}),flush=True)\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":11,"cached_input_tokens":3,"output_tokens":2,"total_tokens":13}}),flush=True)\n'); self.command.chmod(self.command.stat().st_mode|stat.S_IXUSR)
 def tearDown(self): self.temp.cleanup()
 def context(self,prompt="first",timeout=5):
  return AdapterRunContext(task=TaskSpec(task_id="001-file",title="test"),workspace=self.workspace,sandbox=self.sandbox,prompt=prompt,prompt_file=self.prompt_file,session_id="bench",timeout_sec=timeout,env={},model_id="codex-gpt-5.4-medium-current-0.139",model_config={"command":str(self.command),"expected_version":"codex-cli 0.139.0","user_codex_home":str(self.source),"provider":"openai","billing_mode":"subscription_oauth","model":"gpt-5.4","model_reasoning_effort":"medium","sync_refreshed_auth":False,"timeout_grace_sec":.1},mode="live")
 def test_isolation_provenance_strict_success_and_resume(self):
  with mock.patch.dict(os.environ,{"OPENAI_API_KEY":"host-secret","TEST_PASSWORD":"host-password"}): first=CodexAdapter().run(self.context()); second=CodexAdapter().run(self.context("second"))
  self.assertTrue(first.ok,first.stderr); self.assertTrue(second.ok,second.stderr); self.assertFalse(first.metadata["resumed"]); self.assertTrue(second.metadata["resumed"])
  self.assertEqual(first.metadata["executable_provenance"]["version"],"codex-cli 0.139.0"); self.assertEqual(len(first.metadata["executable_provenance"]["sha256"]),64)
  self.assertIn("resume",second.command); self.assertIn("11111111-1111-1111-1111-111111111111",second.command); self.assertFalse((self.sandbox/".codex"/"auth.json").exists())
  self.assertTrue(first.metadata["provider_ok"] and first.metadata["model_ok"] and first.metadata["billing_ok"])
  usage=_collect_proxy_usage_summary(self.sandbox/"usage-proxy"/"requests.jsonl","bench"); self.assertEqual(usage["request_count"],2); self.assertEqual(usage["total_tokens"],26)
 def test_failure_event_is_not_success(self):
  result=CodexAdapter().run(self.context("FAIL")); self.assertFalse(result.ok); self.assertTrue(result.metadata["synthetic_proxy_trace"]["failure_event_seen"])
 @unittest.skipUnless(os.name=="posix","process groups require POSIX")
 def test_timeout_cleans_credentials(self):
  result=CodexAdapter().run(self.context("SLEEP",.1)); self.assertFalse(result.ok); self.assertTrue(result.metadata["timed_out"]); self.assertFalse((self.sandbox/".codex"/"auth.json").exists())
 def test_preflight_rejects_wrong_pin(self):
  result=codex_preflight(self.context().model_config|{"model_reasoning_effort":"high"}); self.assertFalse(result["ok"]); self.assertFalse(result["checks"]["reasoning_pin"])
if __name__=="__main__": unittest.main()
