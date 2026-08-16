#!/usr/bin/env python3
"""Offline production-equivalent probe for Claude's single native macOS Bash sandbox."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, stat, subprocess, sys, tempfile, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'evaluation')]
from harnessbench.adapters.claude_code import _keychain_service
from harnessbench.macos_containment import (builtin_sensitive_paths,merged_policy_for_probe,
 native_credential_paths,native_sandbox_policy,native_seatbelt_profile,_sandbox_exec)
from run_claude_security_smoke import (build_probe_script,DENIAL_PROBES,POSITIVE_PROBES,
                                       PLAINTEXT_DENIAL_PROBES,PROBE_PYTHON)

class Quiet(SimpleHTTPRequestHandler):
 def log_message(self,*args): pass

def sha(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1<<20),b''): h.update(block)
 return h.hexdigest()

def keychain_status(service:str)->int:
 # SecKeychainFindGenericPassword with NULL output buffers returns status only;
 # neither the password nor a derivative is materialized or emitted.
 framework='/System/Library/Frameworks/Security.framework/Versions/A/Security'
 fn=ctypes.CDLL(framework).SecKeychainFindGenericPassword
 fn.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,
              ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
 return int(fn(None,len(service.encode()),service.encode(),0,None,None,None,None))

def validate_report(stdout:str)->dict:
 if stdout.count('\n')>1: raise RuntimeError('native probe did not emit sole JSON')
 try: report=json.loads(stdout)
 except json.JSONDecodeError as exc: raise RuntimeError('native probe output is not JSON') from exc
 if set(report)!={'schema','script_exit_status','probes'} or report['schema']!=1 or report['script_exit_status']!=0:
  raise RuntimeError('native probe report header invalid')
 probes=report['probes']
 if tuple(probes)!=DENIAL_PROBES+POSITIVE_PROBES: raise RuntimeError('native probe set/order invalid')
 for name in DENIAL_PROBES:
  item=probes[name]
  expected='os_denial' if name in PLAINTEXT_DENIAL_PROBES else 'credential_unavailable'
  if item.get('observed_status')==0 or item.get('observed_output')!=expected or item.get('passed') is not True:
   raise RuntimeError('native denial failed: '+name)
 for name in POSITIVE_PROBES:
  if probes[name].get('observed_status')!=0 or probes[name].get('passed') is not True:
   raise RuntimeError('native capability failed: '+name)
 return report

def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--benchmark-seed',type=Path,default=Path('~/.harnessbench/claude-code-opus-4.6')); a=ap.parse_args()
 if sys.platform!='darwin': raise SystemExit('native probe requires macOS')
 seed=a.benchmark_seed.expanduser().resolve()
 service=_keychain_service(seed); status_before=keychain_status(service)
 if status_before!=0: raise SystemExit('dedicated exact Keychain service is unavailable')
 with tempfile.TemporaryDirectory(prefix='harnessbench-real-native-probe-') as td:
  sandbox=Path(td).resolve(); workspace=sandbox/'workspace'; (workspace/'in').mkdir(parents=True); (workspace/'out').mkdir(); private=sandbox/'private-evidence'; private.mkdir(); plaintext=private/'.read-sentinel'; plaintext.write_text('HARMLESS-NATIVE-PROBE\n'); plaintext.chmod(0o400); plaintext_hash=sha(plaintext)
  (workspace/'in'/'fixture.txt').write_text('workspace-ok\n'); (workspace/'in'/'image.png').write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de'))
  handler=partial(Quiet,directory=str(workspace)); server=ThreadingHTTPServer(('127.0.0.1',0),handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
  try:
   url=f'http://127.0.0.1:{server.server_port}/in/fixture.txt'
   probe=workspace/'.native-sandbox-probe.sh'
   probe.write_text(build_probe_script(workspace=workspace,plaintext=plaintext,python=PROBE_PYTHON,service=service,url=url,require_proxy_env=False)); probe.chmod(0o400); probe_hash=sha(probe)
   controls=[private]
   policy=native_sandbox_policy(ROOT,seed,workspace=workspace,sandbox=sandbox,binary=Path('/bin/bash'),control_paths=controls)
   builtins=builtin_sensitive_paths(ROOT,seed,workspace=workspace,control_paths=controls)
   credentials=native_credential_paths(seed,workspace=workspace,control_paths=controls)
   merged=merged_policy_for_probe(policy,credentials,builtins)
   completed=subprocess.run([str(_sandbox_exec()),'-p',native_seatbelt_profile(merged),'/bin/bash',str(probe)],cwd=workspace,text=True,capture_output=True,stdin=subprocess.DEVNULL,timeout=60,check=False)
  finally: server.shutdown(); server.server_close(); thread.join(timeout=5)
  if completed.returncode: raise SystemExit(f'native probe failed rc={completed.returncode}: {completed.stderr[-1000:]}')
  report=validate_report(completed.stdout)
  if sha(probe)!=probe_hash or stat.S_IMODE(probe.stat().st_mode)&0o222: raise SystemExit('immutable probe changed')
  if (workspace/'out'/'probe-failure.json').exists(): raise SystemExit('unexpected native probe failure diagnostic')
  if not plaintext.is_file() or sha(plaintext)!=plaintext_hash: raise SystemExit('private denied sentinel changed')
 status_after=keychain_status(service)
 if status_after!=status_before:
  raise SystemExit('parent exact-service lookup status changed')
 # Status is safe evidence; no Keychain secret or secret derivative is materialized.
 print(json.dumps({'schema':1,'status':'passed','single_native_sandbox':True,
  'nested_sandbox':False,'backend':'macos-security-framework-generic-password',
  'service':service,'status_before':status_before,'status_after':status_after,
  'service_exists':status_before==0,'parent_keychain_status_unchanged':True,
  'probe_report':report},separators=(',',':')))
 return 0
if __name__=='__main__': raise SystemExit(main())
