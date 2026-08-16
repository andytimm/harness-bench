from __future__ import annotations
import json, os, shutil, stat, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from harnessbench.adapters.codex import CodexAdapter, _filtered_env, _offline_argument_parse, _remove_auth, _stage_auth, _staged_auth_lifetime, _sync_staged_auth, codex_preflight, provision_run_private_auth, write_codex_events_as_proxy_trace
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_proxy_usage_summary, _proxy_usage_integrity_failure

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
  self.command=self.root/"codex"; self.command.write_text("#!"+sys.executable+"\n"+'import json, os, pathlib, sys, time\nargs = sys.argv[1:]\nif args == ["--version"]:\n print("codex-cli 0.139.0"); raise SystemExit(0)\nif "--help" in args:\n if "--ask-for-approval" in args: print("unknown", file=sys.stderr); raise SystemExit(2)\n print("Usage: codex exec [OPTIONS]"); raise SystemExit(0)\nhome=pathlib.Path(os.environ["CODEX_HOME"])\nassert (home/"auth.json").is_file()\nfor forbidden in ("OPENAI_API_KEY","TEST_PASSWORD","GENERIC_TOKEN","BROWSER_COOKIE","SERVICE_CREDENTIAL","APP_DSN","PRIME_AGENT_INTERNAL_DAEMON_WORKER_TOKEN"):\n assert forbidden not in os.environ, forbidden\nassert "--ask-for-approval" not in args\nassert args[args.index("--model")+1] == "gpt-5.4"\nassert \'model_reasoning_effort="medium"\' in args\nprompt=sys.stdin.read()\nif prompt == "SLEEP": time.sleep(30)\nstate=home/"harnessbench-state.json"\nsid=json.loads(state.read_text())["session_id"] if state.is_file() else "11111111-1111-1111-1111-111111111111"\nsession=home/"sessions"/"2026"/"01"/"rollout.jsonl"; session.parent.mkdir(parents=True,exist_ok=True)\nmeta={"id":sid,"model_provider":"openai","cli_version":"0.139.0"}\nturn={"model":"gpt-5.4","effort":"medium"}\nif prompt == "MISSING_FACTS": meta.pop("cli_version"); turn.pop("effort")\nwith session.open("a") as f:\n f.write(json.dumps({"type":"session_meta","payload":meta})+"\\n")\n f.write(json.dumps({"type":"turn_context","payload":turn})+"\\n")\n f.write(json.dumps({"type":"event_msg","payload":{"type":"token_count"}})+"\\n")\nprint(json.dumps({"type":"thread.started","thread_id":sid}),flush=True)\nprint(json.dumps({"type":"turn.started"}),flush=True)\nif prompt == "FAIL": print(json.dumps({"type":"turn.failed","error":{"message":"bad"}}),flush=True); raise SystemExit(0)\nif prompt == "MALFORMED": print("not-json",flush=True)\nprint(json.dumps({"type":"item.completed","item":{"id":"m","type":"agent_message","text":"done"}}),flush=True)\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":11,"cached_input_tokens":3,"output_tokens":2,"total_tokens":13}}),flush=True)\nif prompt != "NO_LAST": pathlib.Path(args[args.index("--output-last-message")+1]).write_text("done\\n")\n'); self.command.chmod(self.command.stat().st_mode|stat.S_IXUSR)
 def tearDown(self): self.temp.cleanup()
 def context(self,prompt="first",timeout=5):
  return AdapterRunContext(task=TaskSpec(task_id="001-file",title="test"),workspace=self.workspace,sandbox=self.sandbox,prompt=prompt,prompt_file=self.prompt_file,session_id="bench",timeout_sec=timeout,env={},model_id="codex-gpt-5.4-medium-current-0.139",model_config={"adapter":"codex","command":str(self.command),"expected_version":"codex-cli 0.139.0","benchmark_auth_file":str(self.source/"auth.json"),"provider":"openai","billing_mode":"subscription_oauth","model":"gpt-5.4","model_reasoning_effort":"medium","sandbox":"workspace-write","sync_refreshed_auth":False,"timeout_grace_sec":.1},mode="live")
 def test_isolation_provenance_strict_success_and_resume(self):
  host_home=self.root/"host-home"; normal_auth=host_home/".codex"/"auth.json"; normal_auth.parent.mkdir(parents=True); normal_auth.write_bytes(b"host-codex-auth-must-not-change"); before=normal_auth.read_bytes()
  with mock.patch.dict(os.environ,{"HOME":str(host_home),"OPENAI_API_KEY":"host-secret","TEST_PASSWORD":"host-password"}): first=CodexAdapter().run(self.context()); second=CodexAdapter().run(self.context("second"))
  self.assertEqual(normal_auth.read_bytes(),before)
  self.assertTrue(first.ok,first.stderr); self.assertTrue(second.ok,second.stderr); self.assertFalse(first.metadata["resumed"]); self.assertTrue(second.metadata["resumed"])
  self.assertEqual(first.metadata["executable_provenance"]["version"],"codex-cli 0.139.0"); self.assertEqual(len(first.metadata["executable_provenance"]["sha256"]),64)
  self.assertIn("resume",second.command); self.assertIn("11111111-1111-1111-1111-111111111111",second.command); self.assertFalse((self.sandbox/".codex"/"auth.json").exists())
  self.assertTrue(first.metadata["provider_ok"] and first.metadata["model_ok"] and first.metadata["reasoning_ok"] and first.metadata["billing_ok"] and first.metadata["last_message_ok"]); self.assertNotIn("--ask-for-approval",first.command)
  usage=_collect_proxy_usage_summary(self.sandbox/"usage-proxy"/"requests.jsonl","bench"); self.assertEqual(usage["request_count"],2); self.assertEqual(usage["total_tokens"],26)
 def test_failure_event_is_not_success(self):
  result=CodexAdapter().run(self.context("FAIL")); self.assertFalse(result.ok); self.assertTrue(result.metadata["synthetic_proxy_trace"]["failure_event_seen"])
 @unittest.skipUnless(os.name=="posix","process groups require POSIX")
 def test_timeout_cleans_credentials(self):
  result=CodexAdapter().run(self.context("SLEEP",.1)); self.assertFalse(result.ok); self.assertTrue(result.metadata["timed_out"]); self.assertFalse((self.sandbox/".codex"/"auth.json").exists())
 def test_preflight_rejects_wrong_pin(self):
  result=codex_preflight(self.context().model_config|{"model_reasoning_effort":"high"}); self.assertFalse(result["ok"]); self.assertFalse(result["checks"]["reasoning_pin"])
  version=codex_preflight(self.context().model_config|{"expected_version":"codex-cli 9.9.9"}); self.assertFalse(version["ok"]); self.assertFalse(version["checks"]["expected_version_pin"])
 def test_normal_host_auth_path_is_rejected(self):
  result=codex_preflight(self.context().model_config|{"benchmark_auth_file":str(Path.home()/".codex"/"auth.json")}); self.assertFalse(result["ok"]); self.assertFalse(result["checks"]["dedicated_auth_path"])
  nested=codex_preflight(self.context().model_config|{"benchmark_auth_file":str(Path.home()/".codex"/"benchmark-auth.json")}); self.assertFalse(nested["ok"]); self.assertFalse(nested["checks"]["dedicated_auth_path"])

 def test_strict_last_message_and_malformed_stdout(self):
  malformed=CodexAdapter().run(self.context("MALFORMED")); self.assertFalse(malformed.ok); self.assertFalse(malformed.metadata["stdout_json_ok"])
  no_last=CodexAdapter().run(self.context("NO_LAST")); self.assertFalse(no_last.ok); self.assertFalse(no_last.metadata["last_message_ok"])
  shutil.rmtree(self.sandbox/".codex",ignore_errors=True); missing=CodexAdapter().run(self.context("MISSING_FACTS")); self.assertFalse(missing.ok); self.assertFalse(missing.metadata["reasoning_ok"]); self.assertFalse(missing.metadata["native_version_ok"])
 def test_controls_strictly_allowlisted(self):
  for addition in ({"extra_args":["--search"]},{"config_overrides":["model_provider=evil"]},{"profile":"host"},{"base_url":"https://evil"}):
   result=codex_preflight(self.context().model_config|addition); self.assertFalse(result["ok"]); self.assertFalse(result["checks"]["controls_allowlisted"])
 def test_pre_popen_exception_removes_auth(self):
  with mock.patch("harnessbench.adapters.codex._filtered_env",side_effect=RuntimeError("boom")):
   with self.assertRaises(RuntimeError): CodexAdapter().run(self.context())
  self.assertFalse((self.sandbox/".codex"/"auth.json").exists())


class CodexHardeningTests(unittest.TestCase):
 def auth(self,value="old"): return json.dumps({"tokens":{"access_token":value,"refresh_token":"refresh"}}).encode()
 def test_concurrent_change_and_symlink_safety(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); source=root/"source.json"; source.write_bytes(self.auth())
   stage=_stage_auth(source,root/"target"); stage.target.write_bytes(self.auth("bench"))
   synced,status=_sync_staged_auth(stage); self.assertTrue(synced); self.assertEqual(status,"updated"); self.assertEqual(source.read_bytes(),self.auth("bench")); _remove_auth(stage.target)
   source.write_bytes(self.auth("old")); stage=_stage_auth(source,root/"target"); stage.target.write_bytes(self.auth("bench")); source.write_bytes(self.auth("concurrent"))
   synced,status=_sync_staged_auth(stage); self.assertFalse(synced); self.assertEqual(status,"source_changed"); self.assertEqual(source.read_bytes(),self.auth("concurrent")); _remove_auth(stage.target)
   source.write_bytes(self.auth("old")); stage=_stage_auth(source,root/"locktarget"); stage.target.write_bytes(self.auth("bench")); lock=source.with_suffix(source.suffix+".harnessbench.lock"); lock.unlink(missing_ok=True); lock.symlink_to(root/"missing")
   synced,status=_sync_staged_auth(stage); self.assertFalse(synced); self.assertEqual(status,"io_or_safety_error"); lock.unlink(); _remove_auth(stage.target)
   real=root/"real.json"; real.write_bytes(self.auth()); link=root/"link.json"; link.symlink_to(real)
   with self.assertRaises(ValueError): _stage_auth(link,root/"target2")
   staging_link=root/"staging-link"; staging_link.symlink_to(root,target_is_directory=True)
   with self.assertRaises(ValueError): _stage_auth(real,staging_link)
   stage=_stage_auth(real,root/"target3"); stage.target.unlink(); stage.target.symlink_to(real)
   synced,status=_sync_staged_auth(stage); self.assertFalse(synced); self.assertEqual(status,"io_or_safety_error"); stage.target.unlink()
 def test_run_private_rotation_never_writes_seed_and_exclusive_lock(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); seed=root/"dedicated-seed.json"; seed.write_bytes(self.auth("seed")); private=root/"run"/"auth.json"
   proof=provision_run_private_auth(seed,private); self.assertEqual(proof["created"],"true"); original=seed.read_bytes()
   private.write_bytes(self.auth("rotated")); self.assertEqual(seed.read_bytes(),original)
   first=_staged_auth_lifetime(private,root/"task1"); stage=first.__enter__()
   second=_staged_auth_lifetime(private,root/"task2")
   with self.assertRaises((BlockingIOError,OSError)): second.__enter__()
   first.__exit__(None,None,None); self.assertFalse(stage.target.exists())
 def test_staging_loops_on_partial_writes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); source=root/"auth.json"; source.write_bytes(self.auth("long-value")); real_write=os.write
   def partial(fd,data): return real_write(fd,bytes(data[:max(1,min(3,len(data))) ]))
   with mock.patch("harnessbench.adapters.codex.os.write",side_effect=partial): stage=_stage_auth(source,root/"target")
   self.assertEqual(stage.target.read_bytes(),source.read_bytes()); _remove_auth(stage.target)
 def test_minimal_env_blocks_secrets(self):
  hostile={"SAFE_HOOK":"ok","GENERIC_TOKEN":"x","BROWSER_COOKIE":"x","SERVICE_CREDENTIAL":"x","APP_DSN":"x","PRIME_AGENT_INTERNAL_DAEMON_WORKER_TOKEN":"x"}
  with mock.patch.dict(os.environ,hostile): env,removed=_filtered_env(hostile,["SAFE_HOOK"])
  self.assertEqual(env["SAFE_HOOK"],"ok")
  for key in hostile:
   if key != "SAFE_HOOK": self.assertNotIn(key,env); self.assertIn(key,removed)
  with self.assertRaises(ValueError): _filtered_env({},["MY_SECRET"])
 def test_call_count_bound(self):
  with tempfile.TemporaryDirectory() as tmp:
   log=Path(tmp)/"requests.jsonl"; log.write_text(json.dumps({"call_count":10001})+"\n")
   result=_collect_proxy_usage_summary(log,"bench"); self.assertFalse(result["available"]); self.assertIn("out of bounds",result["reason"]); self.assertTrue(_proxy_usage_integrity_failure(result))

@unittest.skipUnless(shutil.which("codex"),"real Codex CLI unavailable")
class RealCodexOfflineParseTests(unittest.TestCase):
 def test_current_cli_accepts_exact_offline_flags(self):
  result=_offline_argument_parse(str(shutil.which("codex")),"gpt-5.4","medium"); self.assertTrue(result["ok"],result)

if __name__=="__main__": unittest.main()
