#!/usr/bin/env python3
"""Prepare and run two immutable, sequential Claude Code full-suite tranches."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_ID="claude-code-opus-4.6-medium"; MODEL="claude-opus-4-6"; EFFORT="medium"
VERSION="2.1.227 (Claude Code)"; SHA256="7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7"
LIVE_ACK="I_ACKNOWLEDGE_CLAUDE_SUBSCRIPTION_LIVE_EVALUATION"

def now(): return datetime.now(timezone.utc).isoformat()
def immutable_json(path:Path, value:dict[str,Any]):
 path.parent.mkdir(parents=True,exist_ok=True)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
 with os.fdopen(fd,'w') as out: json.dump(value,out,indent=2); out.write('\n'); out.flush(); os.fsync(out.fileno())
def task_number(name:str)->int: return int(name.split('-',1)[0])
def sha(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1<<20),b''): h.update(block)
 return h.hexdigest()
def build_plan(root:Path)->dict[str,Any]:
 tasks=sorted(p.name for p in (root/'tasks').iterdir() if (p/'task.yaml').is_file())
 if len(tasks)!=106 or sorted(task_number(x) for x in tasks)!=list(range(1,107)): raise RuntimeError('benchmark revision must contain numeric tasks 001..106 exactly')
 odd=[x for x in tasks if task_number(x)%2]; even=[x for x in tasks if not task_number(x)%2]
 if len(odd)!=53 or len(even)!=53: raise RuntimeError('tranches must contain 53 tasks each')
 return {'schema':1,'benchmark_root':str(root.resolve()),'target_count':106,'harness':MODEL_ID,'model':MODEL,'effort':EFFORT,'version':VERSION,'sha256':SHA256,'tranches':{'1':odd,'2':even},'selection':{'1':'odd numeric task IDs','2':'even numeric task IDs'},'attempt_policy':'one initial attempt per task; no score-driven retries'}
def validate_result(path:Path,task:str)->dict[str,Any]:
 data=json.loads(path.read_text()); errors=[]
 if data.get('task_id')!=task or data.get('model_id')!=MODEL_ID: errors.append('task/model mismatch')
 rounds=data.get('adapter_results') or []
 if not rounds or not all(r.get('ok') for r in rounds): errors.append('adapter round failed')
 for r in rounds:
  m=r.get('metadata') or {}
  if (m.get('claude_version'),m.get('binary_sha256'),m.get('model'),m.get('effort'))!=(VERSION,SHA256,MODEL,EFFORT): errors.append('pin validation failed')
  if not all(m.get(k) for k in ('session_ids_valid','init_valid','terminal_valid','disabled_features_valid','staged_credential_removed','native_session_file')): errors.append('native terminal/session/security validation failed')
  if m.get('quota_censored'): errors.append('quota_censored')
 usage=data.get('usage_summary') or {}
 if usage.get('models')!=[MODEL] or usage.get('providers')!=['anthropic-subscription-oauth']: errors.append('usage source/model invalid')
 if (data.get('scoring') or {}).get('rubric',{}).get('skipped') is not True: errors.append('process grading was not off')
 if errors: raise RuntimeError('; '.join(errors))
 return {'task_id':task,'result_file':str(path),'usage':usage,'outcome_score':(data.get('oracle_result') or {}).get('outcome_score')}
def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); ap.add_argument('--tranche',choices=['1','2'],required=True); ap.add_argument('--dry-plan',action='store_true'); ap.add_argument('--live',action='store_true'); ap.add_argument('--ack'); ap.add_argument('--benchmark-seed',type=Path,default=Path('~/.harnessbench/claude-code-opus-4.6').expanduser()); a=ap.parse_args()
 root=Path(__file__).resolve().parents[1]; run=a.run_root.expanduser().resolve()
 if run==root or root in run.parents: raise SystemExit('--run-root must be a stable path outside the benchmark checkout')
 plan=build_plan(root); plan_path=run/'plan.json'
 if plan_path.exists():
  if json.loads(plan_path.read_text())!=plan: raise SystemExit('immutable plan differs; choose a new run root')
 else: immutable_json(plan_path,plan)
 print(json.dumps(plan,indent=2))
 if a.dry_plan: return 0
 if not a.live or a.ack!=LIVE_ACK: raise SystemExit(f'live launch requires --live --ack {LIVE_ACK}')
 seed=a.benchmark_seed.expanduser().absolute(); cred=seed/'.credentials.json'
 resolved_seed=seed.resolve()
 if resolved_seed==(Path.home()/'.claude').resolve() or (Path.home()/'.claude').resolve() in resolved_seed.parents or seed.is_symlink() or cred.is_symlink() or not cred.is_file(): raise SystemExit('dedicated non-symlink benchmark OAuth seed is required; normal ~/.claude is forbidden')
 try: oauth=json.loads(cred.read_text()).get('claudeAiOauth',{})
 except (OSError,json.JSONDecodeError): oauth={}
 if not oauth.get('accessToken') or not oauth.get('refreshToken'): raise SystemExit('dedicated subscription OAuth access/refresh tokens are incomplete')
 binary=Path(subprocess.check_output(['command','-v','claude'],text=True,shell=False).strip()).resolve() if False else Path(__import__('shutil').which('claude') or '')
 if not binary.is_file() or sha(binary.resolve())!=SHA256: raise SystemExit('Claude Code resolved binary hash pin failed')
 ver=subprocess.run([str(binary.resolve()),'--version'],text=True,capture_output=True,timeout=10,check=False)
 if ver.returncode or ver.stdout.strip()!=VERSION: raise SystemExit('Claude Code exact version pin failed')
 from harnessbench.macos_containment import verify_repo_containment, verify_task_capabilities
 verify_repo_containment(root); verify_task_capabilities(root,cred)
 cfg={'models':{MODEL_ID:{'adapter':'claude_code','command':str(binary.resolve()),'expected_version':VERSION,'expected_sha256':SHA256,'benchmark_config_seed':str(seed),'model':MODEL,'effort':EFFORT,'billing_mode':'subscription_oauth','timeout_sec':2400,'timeout_grace_sec':5,'sync_refreshed_auth':True,'use_usage_proxy':False,'require_macos_containment':True}}}
 external=run/'control'; external.mkdir(parents=True,exist_ok=True)
 harness_cfg=external/'harness.json'; harness_cfg.write_text(json.dumps(cfg,indent=2)+'\n')
 app_cfg=external/'app.json'; app_cfg.write_text(json.dumps({'tasks_dir':str(root/'tasks'),'data_dir':str(run/'data'),'results_dir':str(run/'results'),'work_root':str(run/'work'),'default_timeout_sec':2400})+'\n')
 env=os.environ.copy(); env.update({'HARNESSBENCH_APP_CONFIG':str(app_cfg),'HARNESSBENCH_HARNESS_CONFIG':str(harness_cfg),'HARNESSBENCH_SKIP_PROCESS_GRADE':'1','HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM':'1','HARNESSBENCH_PUBLIC_URL_TEMPLATE':'{local_url}','PYTHONPATH':str(root/'src')})
 tasks=plan['tranches'][a.tranche]; receipts=run/'receipts'; claims=run/'claims'; results=run/'results'/MODEL_ID
 for index,task in enumerate(tasks,1):
  receipt=receipts/f'{task}.json'
  if receipt.exists(): print(f'[{index}/53] {task}: immutable receipt present'); continue
  claim=claims/f'{task}.json'
  if claim.exists(): raise SystemExit(f'{task} has an immutable launch claim but no receipt; do not retry automatically (manual adjudication required)')
  immutable_json(claim,{'task_id':task,'tranche':a.tranche,'attempt':1,'claimed_at':now(),'policy':'initial attempt; never score-driven retry'})
  command=[str(root/'.venv/bin/python'),'-m','harnessbench.cli','run-task','--task',task,'--harness',MODEL_ID,'--mode','live']
  completed=subprocess.run(command,env=env,stdin=subprocess.DEVNULL)
  matches=list(results.glob(f'*/{task}.json'))
  if len(matches)==1:
   raw=json.loads(matches[0].read_text())
   quota=any((r.get('metadata') or {}).get('quota_censored') for r in (raw.get('adapter_results') or []))
   if quota:
    immutable_json(receipt,{'task_id':task,'status':'quota_censored','finished_at':now(),'result_file':str(matches[0])})
    raise SystemExit('quota rejected: receipt marked quota_censored; scheduling stopped')
  if completed.returncode or len(matches)!=1: raise SystemExit(f'{task} launch failed; claim retained and no automatic retry permitted')
  record=validate_result(matches[0],task)
  immutable_json(receipt,{**record,'status':'complete','finished_at':now(),'claim_file':str(claim)})
 return 0
if __name__=='__main__': raise SystemExit(main())
