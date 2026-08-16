from __future__ import annotations
import json, os, stat, sys, tempfile, textwrap, unittest
from pathlib import Path
from unittest import mock
from harnessbench.adapters.claude_code import ClaudeCodeAdapter, EXPECTED_SHA256
from harnessbench.models import AdapterRunContext, TaskSpec
from harnessbench.runner import _collect_proxy_usage_summary

class ClaudeCodeAdapterTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name); self.sandbox=self.root/'sandbox'; self.workspace=self.sandbox/'workspace'; self.workspace.mkdir(parents=True); (self.workspace/'in').mkdir(); (self.workspace/'out').mkdir(); self.prompt_file=self.sandbox/'prompt.txt'; self.prompt_file.write_text('prompt')
  self.seed=self.root/'seed'; self.seed.mkdir(mode=0o700); (self.seed/'.credentials.json').write_text('{"claudeAiOauth":{"accessToken":"seed","refreshToken":"refresh"}}'); (self.seed/'.credentials.json').chmod(0o600)
  self.command=self.root/'fake-claude'
  script='''#!{python}
import json,os,pathlib,sys,time
args=sys.argv[1:]
if "--version" in args: print("2.1.227 (Claude Code)"); raise SystemExit()
def value(flag): return args[args.index(flag)+1]
assert "--bare" not in args and value("--model")=="claude-opus-4-6" and value("--effort")=="medium"
assert value("--output-format")=="stream-json" and value("--setting-sources")==""
assert "--strict-mcp-config" in args and "--disable-slash-commands" in args
assert "ANTHROPIC_API_KEY" not in os.environ
config=pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]); assert not (config/".credentials.json").is_symlink()
settings=json.loads(pathlib.Path(value("--settings")).read_text()); assert settings["sandbox"]["failIfUnavailable"] is True; assert settings["sandbox"]["credentials"]["files"][0]["mode"]=="deny"
session=value("--resume") if "--resume" in args else value("--session-id"); prompt=args[-1]
if prompt=="SLEEP": time.sleep(30)
print(json.dumps({{"type":"system","subtype":"init","session_id":session,"model":"claude-opus-4-6","claude_code_version":"2.1.227","permissionMode":"dontAsk","mcp_servers":[],"plugins":[],"skills":[],"slash_commands":[]}}))
print(json.dumps({{"type":"assistant","session_id":session,"message":{{"role":"assistant","content":[{{"type":"text","text":"done"}}]}}}}))
resumed="--resume" in args; turns=4 if resumed else 2; inp=20 if resumed else 10; out=6 if resumed else 3
print(json.dumps({{"type":"result","subtype":"success" if prompt!="BAD" else "error_max_turns","is_error":prompt=="BAD","session_id":session,"num_turns":turns,"modelUsage":{{"claude-opus-4-6":{{"inputTokens":inp,"outputTokens":out,"cacheReadInputTokens":4,"cacheCreationInputTokens":1}}}}}}))
transcript=config/"projects"/"fake"/(session+".jsonl"); transcript.parent.mkdir(parents=True,exist_ok=True); transcript.write_text("{{}}\\n")
(config/".credentials.json").write_text('{{"claudeAiOauth":{{"accessToken":"refreshed","refreshToken":"refresh"}}}}')
'''.format(python=sys.executable)
  self.command.write_text(script); self.command.chmod(self.command.stat().st_mode|stat.S_IXUSR)
 def tearDown(self): self.t.cleanup()
 def context(self,prompt='go',timeout=5,**changes):
  cfg={'adapter':'claude_code','command':str(self.command),'expected_version':'2.1.227 (Claude Code)','expected_sha256':EXPECTED_SHA256,'benchmark_config_seed':str(self.seed),'model':'claude-opus-4-6','effort':'medium','sync_refreshed_auth':True,'timeout_grace_sec':.1}; cfg.update(changes)
  return AdapterRunContext(task=TaskSpec(task_id='001-file',title='t'),workspace=self.workspace,sandbox=self.sandbox,prompt=prompt,prompt_file=self.prompt_file,session_id='bench-session',timeout_sec=timeout,env={'MOCK_FORM_URL':'http://127.0.0.1:2'},model_id='claude-code-opus-4.6-medium',model_config=cfg,mode='live')
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_isolation_resume_refresh_usage(self,_):
  old=os.environ.get('ANTHROPIC_API_KEY'); os.environ['ANTHROPIC_API_KEY']='no'
  try: first=ClaudeCodeAdapter().run(self.context('first')); second=ClaudeCodeAdapter().run(self.context('second'))
  finally:
   if old is None: os.environ.pop('ANTHROPIC_API_KEY',None)
   else: os.environ['ANTHROPIC_API_KEY']=old
  self.assertTrue(first.ok,first.stderr); self.assertTrue(second.ok,second.stderr); self.assertFalse(first.metadata['resumed']); self.assertTrue(second.metadata['resumed']); self.assertEqual(first.metadata['native_session_id'],second.metadata['native_session_id']); self.assertFalse((self.sandbox/'.claude-benchmark'/'.credentials.json').exists()); self.assertEqual(json.loads((self.seed/'.credentials.json').read_text())['claudeAiOauth']['accessToken'],'refreshed')
  usage=_collect_proxy_usage_summary(self.sandbox/'usage-proxy'/'requests.jsonl','bench-session'); self.assertEqual(usage['request_count'],4); self.assertEqual(usage['total_tokens'],26); self.assertEqual(usage['models'],['claude-opus-4-6'])
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_terminal_and_reserved_failures(self,_):
  self.assertFalse(ClaudeCodeAdapter().run(self.context('BAD')).ok); self.assertFalse(ClaudeCodeAdapter().run(self.context(extra_args=['--bare'])).ok)
 def test_hash_mismatch(self):
  result=ClaudeCodeAdapter().run(self.context()); self.assertFalse(result.ok); self.assertIn('SHA256 mismatch',result.stderr)
 @unittest.skipUnless(os.name=='posix','POSIX')
 @mock.patch('harnessbench.adapters.claude_code._sha256',return_value=EXPECTED_SHA256)
 def test_timeout_cleanup(self,_):
  result=ClaudeCodeAdapter().run(self.context('SLEEP',timeout=.05)); self.assertTrue(result.metadata['timed_out']); self.assertTrue(Path(result.metadata['stdout_log_file']).is_file()); self.assertFalse((self.sandbox/'.claude-benchmark'/'.credentials.json').exists())
if __name__=='__main__': unittest.main()
