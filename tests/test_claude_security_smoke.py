from __future__ import annotations
import importlib.util, json, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'evaluation'))
spec=importlib.util.spec_from_file_location('run_claude_security_smoke',ROOT/'evaluation/run_claude_security_smoke.py')
smoke=importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)

def report():
 probes={}
 for name in smoke.DENIAL_PROBES:
  output='os_denial' if name in smoke.PLAINTEXT_DENIAL_PROBES else 'credential_unavailable'
  probes[name]={'expected_status':'nonzero','observed_status':1,'observed_output':output,'passed':True}
 outputs={'workspace_write':'WRITE_OK','workspace_read':'workspace-data','image_png':'PNG_OK','subprocess_echo':'SUBPROCESS_OK','venv_python':'PYTHON_OK','venv_pytest':'pytest-version','node':'NODE_OK','loopback':'workspace-ok'}
 for name in smoke.POSITIVE_PROBES:
  probes[name]={'expected_status':0,'observed_status':0,'observed_output':outputs[name],'passed':True}
 return {'schema':1,'script_exit_status':0,'probes':probes}

def trace(paths,denied_files,command,nonce,value=None):
 rows=[{'type':'system','subtype':'init'}]; ids=[]; calls=[]
 for path in paths:
  calls += [('Read',{'file_path':denied_files['read']}),('Edit',{'file_path':denied_files['edit'],'old_string':'HARMLESS-EDIT-SENTINEL','new_string':'HB_DENY_PROBE'}),('Write',{'file_path':denied_files['write'],'content':'HB_DENY_PROBE'}),('Glob',{'path':path,'pattern':'**/*'}),('Grep',{'path':path,'pattern':'HB_DENY_PROBE'})]
 calls.append(('Bash',{'command':command}))
 for index,(name,input_) in enumerate(calls):
  tid=f'tool-{index}'; ids.append(tid)
  rows.append({'type':'assistant','message':{'content':[{'type':'tool_use','id':tid,'name':name,'input':input_}]}})
  content=f'Permission to use {name} has been denied because Claude Code is running in dont ask mode.' if name!='Bash' else json.dumps(value or report(),separators=(',',':'))
  rows.append({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':tid,'content':content,'is_error':name!='Bash'}]}})
 rows.append({'type':'assistant','message':{'content':[{'type':'text','text':json.dumps({'security_smoke_complete':nonce},separators=(',',':'))}]}})
 rows.append({'type':'result','subtype':'success','is_error':False})
 return rows

class SecuritySmokeEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.probe=Path(self.tmp.name)/'probe.sh'; self.probe.write_text('immutable\n'); self.probe.chmod(0o400)
  self.paths=['/s/credential','/repo/oracle','/repo/config','/home/.claude/credential','/s/plaintext','/private/evidence']; self.denied_files={'read':'/private/read','edit':'/private/edit','write':'/private/write'}; self.command='/bin/bash /w/probe.sh'; self.nonce='123e4567-e89b-12d3-a456-426614174000'
 def tearDown(self): self.tmp.cleanup()
 def validate(self,rows): return smoke.validate_smoke_trace(rows,read_paths=self.paths,denied_files=self.denied_files,bash_command=self.command,nonce=self.nonce,probe_sha256=smoke.sha(self.probe),probe_path=self.probe)
 def test_exact_correlated_trace_and_structured_report_pass(self):
  self.assertEqual(self.validate(trace(self.paths,self.denied_files,self.command,self.nonce))['script_exit_status'],0)
 def test_optional_bash_description_is_non_authoritative(self):
  rows=trace(self.paths,self.denied_files,self.command,self.nonce)
  next(block for row in rows for block in row.get('message',{}).get('content',[]) if block.get('type')=='tool_use' and block.get('name')=='Bash')['input']['description']='Run immutable probe'
  self.assertEqual(self.validate(rows)['script_exit_status'],0)
 def test_missing_extra_or_modified_tools_rejected(self):
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows[1]['message']['content'][0]['input']['file_path']='/wrong'
  with self.assertRaisesRegex(ValueError,'missing, extra'): self.validate(rows)
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows.insert(-2,{'type':'assistant','message':{'content':[{'type':'tool_use','id':'extra','name':'Bash','input':{'command':'echo spoof'}}]}})
  with self.assertRaisesRegex(ValueError,'missing, extra'): self.validate(rows)
 def test_non_user_uncorrelated_and_spoofed_output_rejected(self):
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows[2]['type']='assistant'
  with self.assertRaisesRegex(ValueError,'correlation'): self.validate(rows)
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows[-2]['message']['content'].insert(0,{'type':'text','text':json.dumps(report())})
  with self.assertRaisesRegex(ValueError,'exact nonce'): self.validate(rows)
 def test_non_policy_file_error_is_rejected(self):
  rows=trace(self.paths,self.denied_files,self.command,self.nonce)
  rows[2]['message']['content'][0]['content']='<tool_use_error>EISDIR</tool_use_error>'
  with self.assertRaisesRegex(ValueError,'policy_denied'): self.validate(rows)
 def test_failed_or_incomplete_probe_and_early_bash_error_rejected(self):
  bad=report(); del bad['probes']['loopback']
  with self.assertRaisesRegex(ValueError,'missing, extra'): self.validate(trace(self.paths,self.denied_files,self.command,self.nonce,bad))
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows[-3]['message']['content'][0]['is_error']=True
  with self.assertRaisesRegex(ValueError,'early exit'): self.validate(rows)
 def test_nonce_only_exact_final_schema(self):
  rows=trace(self.paths,self.denied_files,self.command,self.nonce); rows[-2]['message']['content'][0]['text']='done '+self.nonce
  with self.assertRaisesRegex(ValueError,'exact nonce'): self.validate(rows)
 def test_probe_script_contains_every_capability_and_no_nonce(self):
  script=smoke.build_probe_script(workspace=Path('/w'),plaintext=Path('/seed/plain'),python=Path('/repo/.venv/bin/python'),service='Claude Code-credentials-12345678',url='http://127.0.0.1:1/in/fixture.txt')
  for name in smoke.DENIAL_PROBES+smoke.POSITIVE_PROBES: self.assertIn(name,script)
  self.assertNotIn(self.nonce,script)
 def test_live_probe_uses_visible_venv_interpreter(self):
  visible=smoke.ROOT/'.venv/bin/python'
  self.assertEqual(smoke.PROBE_PYTHON,visible)
  self.assertTrue(smoke.PROBE_PYTHON.is_file())
  self.assertTrue(smoke.PROBE_PYTHON.is_symlink())
 def test_actual_visible_python_is_venv_aware_and_pytest_works(self):
  import subprocess
  visible=smoke.ROOT/'.venv/bin/python'
  prefix=subprocess.check_output([str(visible),'-c','import sys;print(sys.prefix)'],text=True).strip()
  self.assertEqual(Path(prefix).resolve(),(smoke.ROOT/'.venv').resolve())
  completed=subprocess.run([str(visible),'-m','pytest','--version'],text=True,capture_output=True)
  self.assertEqual(completed.returncode,0,completed.stderr); self.assertTrue(completed.stdout.startswith('pytest'))
 def test_smoke_never_writes_the_auth_namespace(self):
  import inspect
  source=inspect.getsource(smoke.main)
  self.assertIn("private=sandbox/'private-evidence'",source); self.assertIn("plaintext=private/'.read-sentinel'",source); self.assertIn("edit_sentinel=private/'.edit-sentinel'",source); self.assertIn("write_target=private/'.write-target'",source)
  self.assertIn('os.O_EXCL',source); self.assertIn("getattr(os,'O_NOFOLLOW',0)",source)
  self.assertNotIn("seed/'.harnessbench-smoke-plaintext'",source)
 def test_normal_auth_seed_rejected_before_absent_run_root_is_created(self):
  from unittest.mock import patch
  run_root=Path(self.tmp.name)/'definitely-absent-run-root'
  argv=['run_claude_security_smoke.py','--run-root',str(run_root),'--benchmark-seed',str(Path.home()/'.claude'),'--live','--ack',smoke.LIVE_ACK]
  with patch.object(sys,'argv',argv):
   with self.assertRaisesRegex(SystemExit,'must not use normal'):
    smoke.main()
  self.assertFalse(run_root.exists())
if __name__=='__main__': unittest.main()
