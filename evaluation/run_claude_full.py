#!/usr/bin/env python3
"""Prepare and run two immutable, sequential Claude Code full-suite tranches."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, tempfile, shutil, unicodedata, stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from harnessbench.adapters.claude_code import CLAUDE_PLAN_BINDING_KEYS

MODEL_ID="claude-code-opus-4.6-medium"; MODEL="claude-opus-4-6"; EFFORT="medium"
VERSION="2.1.227 (Claude Code)"; SHA256="7432511ba3be818e01f23f6eef8630d214a8b618451e188c3c7d61a987eef6c7"
LIVE_ACK="I_ACKNOWLEDGE_CLAUDE_SUBSCRIPTION_LIVE_EVALUATION"

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(value:Any)->bytes: return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()
def digest(value:Any)->str: return hashlib.sha256(canonical(value)).hexdigest()
def immutable_json(path:Path, value:dict[str,Any]):
 path.parent.mkdir(parents=True,exist_ok=True)
 data=json.dumps(value,indent=2,ensure_ascii=False).encode()+b'\n'
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o400)
 try:
  os.fchmod(fd,0o400); os.write(fd,data); os.fsync(fd)
 finally: os.close(fd)
 dirfd=os.open(path.parent,os.O_RDONLY)
 try: os.fsync(dirfd)
 finally: os.close(dirfd)
def tree_sha(path:Path)->str:
 h=hashlib.sha256()
 for item in sorted((x for x in path.rglob('*') if x.is_file()),key=lambda x:x.relative_to(path).as_posix()):
  rel=item.relative_to(path).as_posix().encode(); h.update(len(rel).to_bytes(4,'big')); h.update(rel); h.update(bytes.fromhex(sha(item)))
 return h.hexdigest()
def git_sha(root:Path)->str:
 value=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
 if not __import__('re').fullmatch(r'[0-9a-f]{40}',value): raise RuntimeError('invalid benchmark git SHA')
 return value

def task_number(name:str)->int: return int(name.split('-',1)[0])
def sha(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1<<20),b''): h.update(block)
 return h.hexdigest()
def runtime_identity(seed:Path, binary:Path|None=None)->dict[str,str]:
 seed=Path(unicodedata.normalize("NFC",str(seed.expanduser().resolve())))
 binary=(binary or Path(shutil.which("claude") or "")).resolve()
 if not binary.is_file(): raise RuntimeError("resolved Claude binary is unavailable")
 binary_hash=sha(binary)
 version=subprocess.run([str(binary),"--version"],text=True,capture_output=True,timeout=10,check=False)
 actual_version=(version.stdout.strip() or version.stderr.strip()) if version.returncode==0 else ""
 if binary_hash!=SHA256 or actual_version!=VERSION: raise RuntimeError("resolved Claude binary version/hash pin failed")
 service="Claude Code-credentials-"+hashlib.sha256(str(seed).encode()).hexdigest()[:8]
 return {"canonical_benchmark_seed":str(seed),"canonical_config_namespace":str(seed),
         "keychain_service":service,"binary":str(binary),"binary_version":actual_version,
         "binary_sha256":binary_hash}

def build_plan(root:Path,seed:Path|None=None,binary:Path|None=None)->dict[str,Any]:
 identity=runtime_identity(seed or Path("~/.harnessbench/claude-code-opus-4.6"),binary)
 tasks=sorted(p.name for p in (root/'tasks').iterdir() if (p/'task.yaml').is_file())
 if len(tasks)!=106 or sorted(task_number(x) for x in tasks)!=list(range(1,107)): raise RuntimeError('benchmark revision must contain numeric tasks 001..106 exactly')
 odd=[x for x in tasks if task_number(x)%2]; even=[x for x in tasks if not task_number(x)%2]
 if len(odd)!=53 or len(even)!=53: raise RuntimeError('tranches must contain 53 tasks each')
 task_integrity={}
 for name in tasks:
  directory=root/'tasks'/name
  task_integrity[name]={'task_yaml_sha256':sha(directory/'task.yaml'),'tree_sha256':tree_sha(directory)}
 body={'schema':3,'benchmark_root':str(root.resolve()),'benchmark_git_sha':git_sha(root),'target_count':106,
       'harness':MODEL_ID,'model':MODEL,'effort':EFFORT,'version':VERSION,
       **identity,'sha256':identity['binary_sha256'],'tasks':task_integrity,'tranches':{'1':odd,'2':even},
       'selection':{'1':'odd numeric task IDs','2':'even numeric task IDs'},
       'attempt_policy':'one initial attempt per task; no score-driven retries'}
 return {**body,'plan_digest':digest(body)}

def plan_binding(plan:dict[str,Any])->dict[str,str]:
 return {key:str(plan[key]) for key in CLAUDE_PLAN_BINDING_KEYS}

def adapter_model_config(plan:dict[str,Any])->dict[str,Any]:
 """Build and self-check the exact binding consumed by the paid adapter."""
 binding=plan_binding(plan)
 cfg={'adapter':'claude_code','command':binding['binary'],'expected_version':VERSION,
      'expected_sha256':SHA256,'benchmark_config_seed':binding['canonical_benchmark_seed'],
      'canonical_benchmark_seed':binding['canonical_benchmark_seed'],
      'canonical_config_namespace':binding['canonical_config_namespace'],
      'keychain_service':binding['keychain_service'],'model':MODEL,'effort':EFFORT,
      'billing_mode':'subscription_oauth','timeout_sec':2400,'timeout_grace_sec':5,
      'sync_refreshed_auth':True,'use_usage_proxy':False,'require_macos_containment':True,
      'evaluation_plan_digest':binding['plan_digest'],'benchmark_git_sha':binding['benchmark_git_sha'],
      'evaluation_plan_binding':binding}
 # This check occurs before any launch claim is written.
 canonical_seed=unicodedata.normalize('NFC',str(Path(cfg['benchmark_config_seed']).expanduser().resolve()))
 projected={
  'plan_digest':str(cfg['evaluation_plan_digest']),
  'benchmark_git_sha':str(cfg['benchmark_git_sha']),
  'canonical_benchmark_seed':canonical_seed,
  'canonical_config_namespace':canonical_seed,
  'keychain_service':'Claude Code-credentials-'+hashlib.sha256(canonical_seed.encode()).hexdigest()[:8],
  'binary':str(Path(cfg['command']).resolve()),'binary_version':VERSION,
  'binary_sha256':SHA256,'model':MODEL,'effort':EFFORT}
 if projected!=binding or cfg['evaluation_plan_binding']!=binding:
  raise RuntimeError('paid adapter configuration is not bound to the exact immutable plan')
 return cfg

def _contained_artifact(value:str,sandbox:Path,expected:Path)->Path:
 path=Path(value)
 if not path.is_absolute() or path.is_symlink() or path.resolve()!=expected.resolve():
  raise RuntimeError(f'artifact path mismatch: {value}')
 try: path.resolve().relative_to(sandbox.resolve())
 except ValueError: raise RuntimeError(f'artifact escapes expected sandbox: {value}')
 info=path.stat()
 if not stat.S_ISREG(info.st_mode): raise RuntimeError(f'artifact is not a regular file: {value}')
 return path

def validate_result(path:Path,task:str,plan_digest:str='',expected_binding:dict[str,str]|None=None)->dict[str,Any]:
 from harnessbench.adapters.claude_code import _parse_stream,_parse_native_transcript
 data=json.loads(path.read_text()); errors=[]
 if data.get('task_id')!=task or data.get('model_id')!=MODEL_ID: errors.append('task/model mismatch')
 sandbox=Path(str(data.get('sandbox') or ''))
 if not sandbox.is_absolute() or not sandbox.is_dir(): errors.append('expected sandbox unavailable')
 rounds=data.get('adapter_results') or []
 if not rounds or not all(r.get('ok') for r in rounds): errors.append('adapter round failed')
 native_session=''; artifact_receipts=[]
 for number,r in enumerate(rounds,1):
  m=r.get('metadata') or {}
  if (m.get('claude_version'),m.get('binary_sha256'),m.get('model'),m.get('effort'))!=(VERSION,SHA256,MODEL,EFFORT): errors.append('pin validation failed')
  if expected_binding is not None and m.get('plan_binding')!=expected_binding: errors.append('adapter plan binding mismatch')
  if plan_digest and m.get('evaluation_plan_digest')!=plan_digest: errors.append('adapter plan digest mismatch')
  if m.get('round_number')!=number or bool(m.get('resumed'))!=(number>1): errors.append('round/resume correlation failed')
  current=str(m.get('native_session_id') or '')
  if native_session and current!=native_session: errors.append('native resume session changed')
  native_session=current or native_session
  if not all(m.get(k) for k in ('session_ids_valid','init_valid','terminal_valid','disabled_features_valid',
                                  'staged_credential_removed','native_session_file','native_transcript_sha256',
                                  'normalized_trace_sha256','stdout_sha256','stderr_sha256','safe_mode','chrome_disabled','canonical_config_namespace','native_sandbox_settings_valid','native_sandbox_runtime_evidence','builtin_file_tool_denies_valid','tool_list_valid')):
   errors.append('native terminal/session/security validation failed')
  if m.get('keychain_cleanup_proven') is not False or m.get('stream_parse_error') or m.get('normalization_error'):
   errors.append('credential/transcript proof invalid')
  if m.get('quota_censored'): errors.append('quota_censored')
  try:
   stdout=_contained_artifact(m['stdout_log_file'],sandbox,sandbox/f'claude-round{number}.stdout.jsonl')
   stderr=_contained_artifact(m['stderr_log_file'],sandbox,sandbox/f'claude-round{number}.stderr.log')
   native=_contained_artifact(m['native_session_file'],sandbox,sandbox/'native-transcripts'/f'round-{number:02d}.jsonl')
   normalized=_contained_artifact(str(sandbox/f'claude-round{number}.normalized.json'),sandbox,sandbox/f'claude-round{number}.normalized.json')
   round_artifacts=[]
   for artifact,key in ((stdout,'stdout_sha256'),(stderr,'stderr_sha256'),(native,'native_transcript_sha256'),(normalized,'normalized_trace_sha256')):
    if sha(artifact)!=m.get(key): raise RuntimeError(f'{key} rehash mismatch')
    round_artifacts.append({'kind':key.removesuffix('_sha256'),'path':str(artifact),'sha256':m[key]})
   stream_rows=_parse_stream(stdout.read_text(encoding='utf-8'))
   if {str(x.get('session_id')) for x in stream_rows if x.get('session_id')}!={current}: raise RuntimeError('stdout native session mismatch')
   native_rows=_parse_native_transcript(native,current)
   stream_uuids={str(x['uuid']) for x in stream_rows if x.get('uuid')}; native_uuids={str(x['uuid']) for x in native_rows if x.get('uuid')}
   if not stream_uuids or not (stream_uuids & native_uuids): raise RuntimeError('native/stdout UUID correlation mismatch')
   normalized_data=json.loads(normalized.read_text(encoding='utf-8'))
   if normalized_data.get('round')!=number or normalized_data.get('native_session_id')!=current: raise RuntimeError('normalized round/session mismatch')
   if [x.get('raw') for x in normalized_data.get('events',[])]!=stream_rows: raise RuntimeError('normalized/stdout correlation mismatch')
   raws=m.get('raw_response_artifacts') or []
   assistants=[x for x in stream_rows if x.get('type')=='assistant']
   if len(raws)!=len(assistants): raise RuntimeError('raw response count mismatch')
   for index,item in enumerate(raws,1):
    raw=_contained_artifact(item.get('path',''),sandbox,sandbox/'usage-proxy'/'responses'/f'claude-round{number:02d}-{index:04d}.json')
    if sha(raw)!=item.get('sha256'): raise RuntimeError('raw response rehash mismatch')
    raw_data=json.loads(raw.read_text(encoding='utf-8'))
    if (raw_data.get('task_id'),raw_data.get('session_id'),raw_data.get('native_session_id'),raw_data.get('source_event_index'))!=(task,data.get('session_id'),current,index): raise RuntimeError('raw response correlation mismatch')
    if Path(raw_data.get('source_stdout_log_file','')).resolve()!=stdout.resolve(): raise RuntimeError('raw/stdout correlation mismatch')
    round_artifacts.append({'kind':'raw_response','path':str(raw),'sha256':item['sha256']})
   artifact_receipts.append({'round':number,'native_session_id':current,'artifacts':round_artifacts})
  except (OSError,ValueError,KeyError,TypeError,RuntimeError,json.JSONDecodeError) as exc: errors.append(str(exc))
 usage=data.get('usage_summary') or {}
 if usage.get('models')!=[MODEL] or usage.get('providers')!=['anthropic-subscription-oauth']: errors.append('usage source/model invalid')
 if (data.get('scoring') or {}).get('rubric',{}).get('skipped') is not True: errors.append('process grading was not off')
 if errors: raise RuntimeError('; '.join(errors))
 return {'task_id':task,'result_file':str(path),'result_sha256':sha(path),'plan_digest':plan_digest,
         'plan_binding':expected_binding or {},'artifact_receipts':artifact_receipts,
         'usage':usage,'outcome_score':(data.get('oracle_result') or {}).get('outcome_score')}
def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); ap.add_argument('--tranche',choices=['1','2'],required=True); ap.add_argument('--dry-plan',action='store_true'); ap.add_argument('--live',action='store_true'); ap.add_argument('--ack'); ap.add_argument('--smoke-approval',type=Path); ap.add_argument('--benchmark-seed',type=Path,default=Path('~/.harnessbench/claude-code-opus-4.6').expanduser()); a=ap.parse_args()
 root=Path(__file__).resolve().parents[1]; run=a.run_root.expanduser().resolve()
 if run==root or root in run.parents: raise SystemExit('--run-root must be a stable path outside the benchmark checkout')
 plan=build_plan(root,a.benchmark_seed); binding=plan_binding(plan); plan_path=run/'plan.json'
 if plan_path.exists():
  if json.loads(plan_path.read_text())!=plan: raise SystemExit('immutable plan differs; choose a new run root')
 else: immutable_json(plan_path,plan)
 print(json.dumps(plan,indent=2))
 if a.dry_plan: return 0
 dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True)
 if dirty: raise SystemExit('live evaluation requires a clean benchmark checkout at the planned git SHA')
 if git_sha(root)!=plan['benchmark_git_sha']: raise SystemExit('benchmark git SHA changed after planning')
 if digest({k:v for k,v in plan.items() if k!='plan_digest'})!=plan['plan_digest']: raise SystemExit('plan digest mismatch')
 if not a.live or a.ack!=LIVE_ACK: raise SystemExit(f'live launch requires --live --ack {LIVE_ACK}')
 if a.smoke_approval is None: raise SystemExit('live launch requires a separately audited --smoke-approval marker')
 approval_path=a.smoke_approval.expanduser()
 try: approval=json.loads(approval_path.read_text())
 except (OSError,json.JSONDecodeError) as exc: raise SystemExit(f'smoke approval marker unavailable/malformed: {exc}')
 expected_approval={'schema':1,'approved':True,'plan_binding':binding}
 if any(approval.get(k)!=v for k,v in expected_approval.items()): raise SystemExit('smoke approval is not bound to this exact immutable plan')
 if approval_path.is_symlink() or stat.S_IMODE(approval_path.stat().st_mode)&0o222: raise SystemExit('smoke approval is not immutable/auditable')
 try:
  smoke_claim=Path(approval['smoke_claim_file']); smoke_receipt=Path(approval['smoke_receipt_file'])
  smoke_data=json.loads(smoke_receipt.read_text())
 except (KeyError,OSError,json.JSONDecodeError) as exc: raise SystemExit(f'audited smoke evidence unavailable: {exc}')
 if (sha(smoke_claim)!=approval.get('smoke_claim_sha256') or sha(smoke_receipt)!=approval.get('smoke_receipt_sha256')
     or smoke_data.get('status')!='passed' or smoke_data.get('plan_binding')!=binding
     or smoke_data.get('claim_sha256')!=sha(smoke_claim) or smoke_data.get('benchmark_claim') is not False
     or smoke_data.get('score') is not None
     or smoke_data.get('security_smoke_marker')!={'native_sandbox_runtime_evidence':True,'all_builtin_file_tools_denied':True,'tool_list_valid':True}): raise SystemExit('audited smoke evidence hash/binding/native-security-marker failed')
 seed=Path(plan['canonical_benchmark_seed']); cred=seed/'.credentials.json'
 resolved_seed=seed.resolve()
 if resolved_seed==(Path.home()/'.claude').resolve() or (Path.home()/'.claude').resolve() in resolved_seed.parents or seed.is_symlink() or cred.is_symlink() or not cred.is_file(): raise SystemExit('dedicated non-symlink benchmark OAuth seed is required; normal ~/.claude is forbidden')
 try: oauth=json.loads(cred.read_text()).get('claudeAiOauth',{})
 except (OSError,json.JSONDecodeError): oauth={}
 if not oauth.get('accessToken') or not oauth.get('refreshToken'): raise SystemExit('dedicated subscription OAuth access/refresh tokens are incomplete')
 binary=Path(plan['binary'])
 if not binary.is_file() or binary.resolve()!=binary or sha(binary)!=plan['binary_sha256']: raise SystemExit('Claude Code resolved binary hash pin failed')
 ver=subprocess.run([str(binary.resolve()),'--version'],text=True,capture_output=True,timeout=10,check=False)
 if ver.returncode or ver.stdout.strip()!=VERSION: raise SystemExit('Claude Code exact version pin failed')
 from harnessbench.macos_containment import verify_repo_containment, verify_task_capabilities
 verify_repo_containment(root); verify_task_capabilities(root,cred,binary=binary)
 paid_cfg=adapter_model_config(plan); paid_cfg['containment_control_roots']=[str(run)]
 cfg={'models':{MODEL_ID:paid_cfg}}
 external=run/'control'; external.mkdir(parents=True,exist_ok=True)
 harness_cfg=external/'harness.json'; harness_cfg.write_text(json.dumps(cfg,indent=2)+'\n')
 app_cfg=external/'app.json'; app_cfg.write_text(json.dumps({'tasks_dir':str(root/'tasks'),'data_dir':str(run/'data'),'results_dir':str(run/'results'),'work_root':str(run/'work'),'default_timeout_sec':2400})+'\n')
 env=os.environ.copy(); env.update({'HARNESSBENCH_APP_CONFIG':str(app_cfg),'HARNESSBENCH_HARNESS_CONFIG':str(harness_cfg),'HARNESSBENCH_SKIP_PROCESS_GRADE':'1','HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM':'1','HARNESSBENCH_PUBLIC_URL_TEMPLATE':'{local_url}','PYTHONPATH':str(root/'src')})
 tasks=plan['tranches'][a.tranche]; receipts=run/'receipts'; claims=run/'claims'; results=run/'results'/MODEL_ID
 for index,task in enumerate(tasks,1):
  receipt=receipts/f'{task}.json'; claim=claims/f'{task}.json'
  matches=list(results.glob(f'*/{task}.json'))
  if receipt.exists():
   try: saved=json.loads(receipt.read_text())
   except (OSError,json.JSONDecodeError) as exc: raise SystemExit(f'{task} receipt malformed: {exc}')
   if saved.get('task_id')!=task or saved.get('plan_digest')!=plan['plan_digest'] or saved.get('plan_binding')!=binding: raise SystemExit(f'{task} receipt is not bound to this plan')
   if len(matches)!=1 or saved.get('result_sha256')!=sha(matches[0]): raise SystemExit(f'{task} retained result hash mismatch')
   if saved.get('claim_sha256')!=sha(claim): raise SystemExit(f'{task} retained claim hash mismatch')
   validate_result(matches[0],task,plan['plan_digest'],binding)
   print(f'[{index}/53] {task}: validated immutable receipt present'); continue
  if matches: raise SystemExit(f'{task} has a result without a validated receipt; refusing overwrite')
  if claim.exists(): raise SystemExit(f'{task} has an immutable launch claim but no receipt; do not retry automatically (manual adjudication required)')
  immutable_json(claim,{'schema':1,'task_id':task,'tranche':a.tranche,'attempt':1,'claimed_at':now(),
                        'plan_digest':plan['plan_digest'],'plan_binding':binding,'task_integrity':plan['tasks'][task],
                        'policy':'initial attempt; never score-driven retry'})
  claim_hash=sha(claim)
  command=[str(root/'.venv/bin/python'),'-m','harnessbench.cli','run-task','--task',task,'--harness',MODEL_ID,'--mode','live']
  completed=subprocess.run(command,env=env,stdin=subprocess.DEVNULL)
  matches=list(results.glob(f'*/{task}.json'))
  if len(matches)==1:
   raw=json.loads(matches[0].read_text())
   quota=any((r.get('metadata') or {}).get('quota_censored') for r in (raw.get('adapter_results') or []))
   if quota:
    immutable_json(receipt,{'task_id':task,'status':'quota_censored','finished_at':now(),'result_file':str(matches[0]),'result_sha256':sha(matches[0]),'claim_sha256':claim_hash,'plan_digest':plan['plan_digest'],'plan_binding':binding})
    raise SystemExit('quota rejected: receipt marked quota_censored; scheduling stopped')
  if completed.returncode or len(matches)!=1: raise SystemExit(f'{task} launch failed; claim retained and no automatic retry permitted')
  record=validate_result(matches[0],task,plan['plan_digest'],binding)
  immutable_json(receipt,{**record,'status':'complete','finished_at':now(),'claim_file':str(claim),'claim_sha256':claim_hash})
 return 0
if __name__=='__main__': raise SystemExit(main())
