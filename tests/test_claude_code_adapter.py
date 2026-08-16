from __future__ import annotations
import json, os, stat, sys, tempfile, textwrap, unittest
from pathlib import Path
from unittest import mock
from harnessbench.adapters.claude_code import (ClaudeCodeAdapter, EXPECTED_SHA256, AUTH_BACKEND,
 KEYCHAIN_STATUS_SEMANTICS, AUTH_STATUS_SEMANTICS, _open_lock as _open_lock_for_test)
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_proxy_usage_summary

class ClaudeCodeAdapterTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name); self.sandbox=self.root/'sandbox'; self.workspace=self.sandbox/'workspace'; self.workspace.mkdir(parents=True); (self.workspace/'in').mkdir(); (self.workspace/'out').mkdir(); self.prompt_file=self.sandbox/'prompt.txt'; self.prompt_file.write_text('prompt')
  self.seed=self.root/'seed'; self.seed.mkdir(mode=0o700)
  self.keychain=mock.patch('harnessbench.adapters.claude_code._keychain_status',return_value=0); self.keychain.start()
  self.command=self.root/'fake-claude'
  script='''#!{python}
import json,os,pathlib,sys,time
args=sys.argv[1:]
if "--version" in args: print("2.1.227 (Claude Code)"); raise SystemExit()
if args==["auth","status","--json"]:
 print(json.dumps({{"loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max","email":"discard@example.test"}})); raise SystemExit()
def value(flag): return args[args.index(flag)+1]
assert "--bare" not in args and value("--model")=="claude-opus-4-6" and value("--effort")=="medium"
assert value("--output-format")=="stream-json" and value("--setting-sources")==""
assert "--strict-mcp-config" in args and "--disable-slash-commands" in args
assert "ANTHROPIC_API_KEY" not in os.environ
config=pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]); assert os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"]==str(config)
settings=json.loads(pathlib.Path(value("--settings")).read_text()); assert settings["sandbox"]["failIfUnavailable"] is True; assert settings["sandbox"]["credentials"]["files"][0]["mode"]=="deny"
session=value("--resume") if "--resume" in args else value("--session-id"); prompt=args[-1]
if prompt=="SLEEP": time.sleep(30)
print(json.dumps({{"type":"system","subtype":"init","session_id":session,"model":"claude-opus-4-6","claude_code_version":"2.1.227","permissionMode":"dontAsk","mcp_servers":[],"plugins":[],"skills":[],"slash_commands":[],"tools":["Read","Edit","Write","Glob","Grep","Bash"]}}))
print(json.dumps({{"type":"assistant","session_id":session,"uuid":"shared-event","message":{{"role":"assistant","content":[{{"type":"text","text":"done"}}]}}}}))
resumed="--resume" in args; turns=4 if resumed else 2; inp=20 if resumed else 10; out=6 if resumed else 3
print(json.dumps({{"type":"result","subtype":"success" if prompt!="BAD" else "error_max_turns","is_error":prompt=="BAD","session_id":session,"num_turns":turns,"modelUsage":{{"claude-opus-4-6":{{"inputTokens":inp,"outputTokens":out,"cacheReadInputTokens":4,"cacheCreationInputTokens":1}}}}}}))
transcript=config/"projects"/"fake"/(session+".jsonl"); transcript.parent.mkdir(parents=True,exist_ok=True); transcript.write_text(json.dumps({{"uuid":"shared-event","sessionId":session}})+"\\n")
'''.format(python=sys.executable)
  self.command.write_text(script); self.command.chmod(self.command.stat().st_mode|stat.S_IXUSR)
 def tearDown(self): self.keychain.stop(); self.t.cleanup()
 def context(self,prompt='go',timeout=5,**changes):
  cfg={'adapter':'claude_code','command':str(self.command),'expected_version':'2.1.227 (Claude Code)','expected_sha256':EXPECTED_SHA256,'benchmark_config_seed':str(self.seed),'model':'claude-opus-4-6','effort':'medium','timeout_grace_sec':.1}; cfg.update(changes)
  return AdapterRunContext(task=TaskSpec(task_id='001-file',title='t'),workspace=self.workspace,sandbox=self.sandbox,prompt=prompt,prompt_file=self.prompt_file,session_id='bench-session',timeout_sec=timeout,env={'MOCK_FORM_URL':'http://127.0.0.1:2'},model_id='claude-code-opus-4.6-medium',model_config=cfg,mode='live')
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_isolation_resume_refresh_usage(self,_):
  old=os.environ.get('ANTHROPIC_API_KEY'); os.environ['ANTHROPIC_API_KEY']='no'
  try: first=ClaudeCodeAdapter().run(self.context('first')); second=ClaudeCodeAdapter().run(self.context('second'))
  finally:
   if old is None: os.environ.pop('ANTHROPIC_API_KEY',None)
   else: os.environ['ANTHROPIC_API_KEY']=old
  self.assertTrue(first.ok,first.stderr); self.assertTrue(second.ok,second.stderr); self.assertFalse(first.metadata['resumed']); self.assertTrue(second.metadata['resumed']); self.assertEqual(first.metadata['native_session_id'],second.metadata['native_session_id']); self.assertFalse(any(p.is_file() and p.name != '.harnessbench.lock' for p in self.seed.iterdir())); self.assertTrue(first.metadata['auth_status_valid'])
  usage=_collect_proxy_usage_summary(self.sandbox/'usage-proxy'/'requests.jsonl','bench-session'); self.assertEqual(usage['request_count'],4); self.assertEqual(usage['total_tokens'],26); self.assertEqual(usage['models'],['claude-opus-4-6'])
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_full_plan_binding_exact_and_mismatch_fails_before_launch(self,_):
  import hashlib, unicodedata
  canonical=unicodedata.normalize('NFC',str(self.seed.resolve()))
  binding={'plan_digest':'d'*64,'benchmark_git_sha':'a'*40,'canonical_benchmark_seed':canonical,
   'canonical_config_namespace':canonical,'auth_backend':AUTH_BACKEND,
    'keychain_service':'Claude Code-credentials-'+hashlib.sha256(canonical.encode()).hexdigest()[:8],
   'keychain_status_semantics':KEYCHAIN_STATUS_SEMANTICS,'auth_status_semantics':AUTH_STATUS_SEMANTICS,
   'binary':str(self.command.resolve()),'binary_version':'2.1.227 (Claude Code)','binary_sha256':EXPECTED_SHA256,
   'model':'claude-opus-4-6','effort':'medium'}
  bad=dict(binding); bad['canonical_benchmark_seed']+='/wrong'
  badctx=self.context(evaluation_plan_digest=binding['plan_digest'],benchmark_git_sha=binding['benchmark_git_sha'],canonical_benchmark_seed=canonical+'/wrong',canonical_config_namespace=canonical,keychain_service=binding['keychain_service'],evaluation_plan_binding=bad)
  with mock.patch('harnessbench.adapters.claude_code._version',return_value='2.1.227 (Claude Code)'), mock.patch('harnessbench.adapters.claude_code.subprocess.Popen') as launch:
   failed=ClaudeCodeAdapter().run(badctx)
  self.assertFalse(failed.ok); self.assertIn('plan binding',failed.stderr); launch.assert_not_called()
  result=ClaudeCodeAdapter().run(self.context(evaluation_plan_digest=binding['plan_digest'],benchmark_git_sha=binding['benchmark_git_sha'],canonical_benchmark_seed=canonical,canonical_config_namespace=canonical,keychain_service=binding['keychain_service'],evaluation_plan_binding=binding))
  self.assertTrue(result.ok,result.stderr); self.assertEqual(result.metadata['plan_binding'],binding)
 def test_valid_fully_bound_paid_result_is_receiptable(self):
  import hashlib, importlib.util, unicodedata
  canonical=unicodedata.normalize('NFC',str(self.seed.resolve()))
  binding={'plan_digest':'d'*64,'benchmark_git_sha':'a'*40,'canonical_benchmark_seed':canonical,
   'canonical_config_namespace':canonical,'auth_backend':AUTH_BACKEND,
    'keychain_service':'Claude Code-credentials-'+hashlib.sha256(canonical.encode()).hexdigest()[:8],
   'keychain_status_semantics':KEYCHAIN_STATUS_SEMANTICS,'auth_status_semantics':AUTH_STATUS_SEMANTICS,
   'binary':str(self.command.resolve()),'binary_version':'2.1.227 (Claude Code)','binary_sha256':EXPECTED_SHA256,'model':'claude-opus-4-6','effort':'medium'}
  def hashes(path):
   path=Path(path)
   if path.resolve()==self.command.resolve(): return EXPECTED_SHA256
   h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()
  with mock.patch('harnessbench.adapters.claude_code._sha256',side_effect=hashes):
   result=ClaudeCodeAdapter().run(self.context(evaluation_plan_digest=binding['plan_digest'],benchmark_git_sha=binding['benchmark_git_sha'],canonical_benchmark_seed=canonical,canonical_config_namespace=canonical,keychain_service=binding['keychain_service'],evaluation_plan_binding=binding))
  self.assertTrue(result.ok,result.stderr)
  payload={'task_id':'001-file','model_id':'claude-code-opus-4.6-medium','sandbox':str(self.sandbox),'session_id':'bench-session','adapter_results':[{'ok':True,'metadata':result.metadata}],
   'usage_summary':{'models':['claude-opus-4-6'],'providers':['anthropic-subscription-oauth']},'scoring':{'rubric':{'skipped':True}},'oracle_result':{'outcome_score':1}}
  result_file=self.root/'result.json'; result_file.write_text(json.dumps(payload))
  spec=importlib.util.spec_from_file_location('receipt_runner',Path(__file__).resolve().parents[1]/'evaluation/run_claude_full.py'); runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
  record=runner.validate_result(result_file,'001-file',binding['plan_digest'],binding)
  self.assertEqual(record['plan_binding'],binding); self.assertEqual(record['task_id'],'001-file')
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_resume_rejects_plan_binding_change(self,_):
  first=ClaudeCodeAdapter().run(self.context('first',evaluation_plan_digest='plan-a',benchmark_git_sha='a'*40)); self.assertTrue(first.ok,first.stderr)
  second=ClaudeCodeAdapter().run(self.context('second',evaluation_plan_digest='plan-b',benchmark_git_sha='a'*40)); self.assertFalse(second.ok); self.assertIn('plan binding mismatch',second.stderr)
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_terminal_and_reserved_failures(self,_):
  self.assertFalse(ClaudeCodeAdapter().run(self.context('BAD')).ok); self.assertFalse(ClaudeCodeAdapter().run(self.context(extra_args=['--bare'])).ok)
 def test_absent_or_changed_exact_service_fails_closed(self):
  self.keychain.stop()
  try:
   with mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256), mock.patch('harnessbench.adapters.claude_code._keychain_status',return_value=-25300):
    missing=ClaudeCodeAdapter().run(self.context())
   self.assertFalse(missing.ok); self.assertIn('Keychain service is unavailable',missing.stderr)
   with mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256), mock.patch('harnessbench.adapters.claude_code._keychain_status',side_effect=[0,-25300]):
    changed=ClaudeCodeAdapter().run(self.context())
   self.assertFalse(changed.ok); self.assertIn('status changed',changed.stderr)
  finally: self.keychain.start()
 def test_status_probes_and_invocation_are_under_namespace_lock(self):
  import fcntl
  self.keychain.stop(); observed=[]
  def status(_service):
   fd=_open_lock_for_test(self.seed/'.harnessbench.lock')
   try:
    with self.assertRaises(BlockingIOError): fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    observed.append(True)
   finally: os.close(fd)
   return 0
  try:
   with mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256), mock.patch('harnessbench.adapters.claude_code._keychain_status',side_effect=status):
    result=ClaudeCodeAdapter().run(self.context())
   self.assertTrue(result.ok,result.stderr); self.assertEqual(observed,[True,True])
  finally: self.keychain.start()
 def test_hash_mismatch(self):
  result=ClaudeCodeAdapter().run(self.context()); self.assertFalse(result.ok); self.assertIn('SHA256 mismatch',result.stderr)
 @unittest.skipUnless(os.name=='posix','POSIX')
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_timeout_cleanup(self,_):
  result=ClaudeCodeAdapter().run(self.context('SLEEP',timeout=.05)); self.assertTrue(result.metadata['timed_out']); self.assertTrue(Path(result.metadata['stdout_log_file']).is_file())
if __name__=='__main__': unittest.main()
