#!/usr/bin/env python3
"""Prepare and run two immutable, sequential Claude Code full-suite tranches."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, tempfile, shutil, unicodedata, stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from harnessbench.adapters.claude_code import (CLAUDE_PLAN_BINDING_KEYS, AUTH_BACKEND,
    KEYCHAIN_STATUS_SEMANTICS, AUTH_STATUS_SEMANTICS, _validate_seed, _namespace_lock,
    _keychain_status, _validate_auth_status)

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
         "auth_backend":AUTH_BACKEND,"keychain_service":service,
         "keychain_status_semantics":KEYCHAIN_STATUS_SEMANTICS,
         "auth_status_semantics":AUTH_STATUS_SEMANTICS,"binary":str(binary),
         "binary_version":actual_version,"binary_sha256":binary_hash}

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
 body={'schema':4,'benchmark_root':str(root.resolve()),'benchmark_git_sha':git_sha(root),'target_count':106,
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
      'auth_backend':binding['auth_backend'],'keychain_service':binding['keychain_service'],
      'keychain_status_semantics':binding['keychain_status_semantics'],
      'auth_status_semantics':binding['auth_status_semantics'],'model':MODEL,'effort':EFFORT,
      'billing_mode':'subscription_oauth','timeout_sec':2400,'timeout_grace_sec':5,
      'use_usage_proxy':False,'require_macos_containment':True,
      'evaluation_plan_digest':binding['plan_digest'],'benchmark_git_sha':binding['benchmark_git_sha'],
      'evaluation_plan_binding':binding}
 # This check occurs before any launch claim is written.
 canonical_seed=unicodedata.normalize('NFC',str(Path(cfg['benchmark_config_seed']).expanduser().resolve()))
 projected={
  'plan_digest':str(cfg['evaluation_plan_digest']),
  'benchmark_git_sha':str(cfg['benchmark_git_sha']),
  'canonical_benchmark_seed':canonical_seed,
  'canonical_config_namespace':canonical_seed,'auth_backend':AUTH_BACKEND,
  'keychain_service':'Claude Code-credentials-'+hashlib.sha256(canonical_seed.encode()).hexdigest()[:8],
  'keychain_status_semantics':KEYCHAIN_STATUS_SEMANTICS,'auth_status_semantics':AUTH_STATUS_SEMANTICS,
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
                                  'auth_status_valid','keychain_status_unchanged','keychain_exists_before','keychain_exists_after','native_session_file','native_transcript_sha256',
                                  'normalized_trace_sha256','stdout_sha256','stderr_sha256','safe_mode','chrome_disabled','canonical_config_namespace','native_sandbox_settings_valid','builtin_file_tool_denies_valid','tool_list_valid')):
   errors.append('native terminal/session/security validation failed')
  if (m.get('auth_backend')!=AUTH_BACKEND or m.get('keychain_service')!=(expected_binding or {}).get('keychain_service')
      or m.get('keychain_status_before')!=m.get('keychain_status_after') or m.get('keychain_status_before')!=0
      or m.get('auth_status_code')!=0 or m.get('native_sandbox_runtime_evidence') is not False
      or m.get('stream_parse_error') or m.get('normalization_error')):
   errors.append('Keychain/auth/transcript proof invalid')
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
def _safe_archive_parent(run:Path,relative:Path)->tuple[Path,int]:
 """Create/open an archive parent without following any destination symlink."""
 run=run.resolve(); archive=run/'archive'; work=archive/'work'
 for base in (archive,work):
  try: info=os.lstat(base)
  except FileNotFoundError:
   os.mkdir(base,0o700)
   info=os.lstat(base)
  if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
   raise RuntimeError('archive destination ancestor is a symlink or non-directory')
 current=work
 for part in relative.parts:
  if part in ('','.','..'): raise RuntimeError('archive destination component is invalid')
  current=current/part
  try: info=os.lstat(current)
  except FileNotFoundError:
   os.mkdir(current,0o700)
   info=os.lstat(current)
  if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
   raise RuntimeError('archive destination ancestor is a symlink or non-directory')
 canonical=current.resolve(strict=True); canonical_work=work.resolve(strict=True)
 if canonical==canonical_work or not canonical.is_relative_to(canonical_work):
  raise RuntimeError('archive destination parent escaped archive/work')
 flags=os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0)
 return canonical,os.open(current,flags)


def _revalidate_archive_parent(run:Path,parent:Path,dirfd:int)->None:
 """Ensure the pinned destination parent still names the reviewed in-tree directory."""
 work=run.resolve()/'archive'/'work'; relative=parent.relative_to(work.resolve(strict=True)); current=work
 for part in relative.parts:
  current=current/part; info=os.lstat(current)
  if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
   raise RuntimeError('archive destination ancestor changed before rename')
 canonical=current.resolve(strict=True); canonical_work=work.resolve(strict=True)
 pinned=os.fstat(dirfd); named=os.stat(current,follow_symlinks=False)
 if (canonical==canonical_work or not canonical.is_relative_to(canonical_work) or
     (pinned.st_dev,pinned.st_ino)!=(named.st_dev,named.st_ino)):
  raise RuntimeError('archive destination parent changed or escaped before rename')


def archive_completed_sandbox(result_file:Path,run:Path)->Path:
 """Move a completed sandbox behind one stable deny prefix, retaining path compatibility."""
 data=json.loads(result_file.read_text()); link=Path(data['sandbox'])
 active=(run/'active').resolve(); archive=(run/'archive'/'work').resolve()
 if link.is_symlink() or not link.is_dir(): raise RuntimeError('completed sandbox is not an unarchived directory')
 resolved=link.resolve()
 try: relative=resolved.relative_to(active)
 except ValueError as exc: raise RuntimeError('completed sandbox escaped active work root') from exc
 parent,dirfd=_safe_archive_parent(run,relative.parent); destination=parent/relative.name
 try:
  _revalidate_archive_parent(run,parent,dirfd)
  try: os.stat(relative.name,dir_fd=dirfd,follow_symlinks=False)
  except FileNotFoundError: pass
  else: raise RuntimeError('completed sandbox archive collision')
  # Revalidate immediately before rename; the O_NOFOLLOW fd pins the directory.
  _revalidate_archive_parent(run,parent,dirfd)
  os.rename(resolved,relative.name,dst_dir_fd=dirfd)
  try: link.symlink_to(destination,target_is_directory=True)
  except OSError:
   os.rename(relative.name,resolved,src_dir_fd=dirfd)
   raise
 finally: os.close(dirfd)
 return destination


def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--run-root',type=Path,required=True); ap.add_argument('--tranche',choices=['1','2'],required=True); ap.add_argument('--dry-plan',action='store_true'); ap.add_argument('--live',action='store_true'); ap.add_argument('--ack'); ap.add_argument('--smoke-approval',type=Path); ap.add_argument('--benchmark-seed',type=Path,default=Path('~/.harnessbench/claude-code-opus-4.6').expanduser()); a=ap.parse_args()
 root=Path(__file__).resolve().parents[1]; run=a.run_root.expanduser().resolve()
 if run==root or root in run.parents: raise SystemExit('--run-root must be a stable path outside the benchmark checkout')
 plan=build_plan(root,a.benchmark_seed); binding=plan_binding(plan); control_plane=run/'control-plane'; plan_path=control_plane/'plan.json'
 if plan_path.exists():
  if json.loads(plan_path.read_text())!=plan: raise SystemExit('immutable plan differs; choose a new run root')
 else: immutable_json(plan_path,plan)
 legacy_plan=run/'plan.json'
 if not legacy_plan.exists() and not legacy_plan.is_symlink(): legacy_plan.symlink_to(plan_path)
 elif legacy_plan.resolve()!=plan_path.resolve(): raise SystemExit('legacy plan link differs; choose a new run root')
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
 seed=Path(plan['canonical_benchmark_seed'])
 try: seed=_validate_seed(seed)
 except ValueError as exc: raise SystemExit(str(exc))
 binary=Path(plan['binary'])
 if not binary.is_file() or binary.resolve()!=binary or sha(binary)!=plan['binary_sha256']: raise SystemExit('Claude Code resolved binary hash pin failed')
 ver=subprocess.run([str(binary.resolve()),'--version'],text=True,capture_output=True,timeout=10,check=False)
 if ver.returncode or ver.stdout.strip()!=VERSION: raise SystemExit('Claude Code exact version pin failed')
 from harnessbench.macos_containment import verify_repo_containment, verify_task_capabilities
 class _Preflight: pass
 pre=_Preflight(); pre.sandbox=run; pre.workspace=run; pre.task=type("T",(),{"task_id":"preflight"})(); pre.session_id="preflight"; pre.model_id=MODEL_ID
 from harnessbench.adapters.claude_code import _clean_env
 auth_env=_clean_env({},seed,pre)
 with _namespace_lock(seed):
  before=_keychain_status(binding['keychain_service'])
  if before!=0: raise SystemExit('dedicated Claude Keychain service is unavailable')
  _validate_auth_status(binary,auth_env)
  after=_keychain_status(binding['keychain_service'])
  if after!=before: raise SystemExit('dedicated Claude Keychain exact-service status changed')
 verify_repo_containment(root); verify_task_capabilities(root,seed,binary=binary)
 external=control_plane/'config'; claims=control_plane/'claims'; receipts=control_plane/'receipts'; results_root=control_plane/'results'; data_root=control_plane/'data'; archive=run/'archive'
 for directory in (external,claims,receipts,results_root,data_root,archive): directory.mkdir(parents=True,exist_ok=True)
 for name,target in (("control",external),("claims",claims),("receipts",receipts),("results",results_root),("data",data_root)):
  legacy=run/name
  if not legacy.exists() and not legacy.is_symlink(): legacy.symlink_to(target,target_is_directory=True)
  elif legacy.resolve()!=target.resolve(): raise SystemExit(f'legacy {name} link differs; choose a new run root')
 paid_cfg=adapter_model_config(plan)
 paid_cfg['containment_control_roots']=[str(control_plane),str(archive)]
 cfg={'models':{MODEL_ID:paid_cfg}}
 harness_cfg=external/'harness.json'; harness_cfg.write_text(json.dumps(cfg,indent=2)+'\n')
 app_cfg=external/'app.json'; app_cfg.write_text(json.dumps({'tasks_dir':str(root/'tasks'),'data_dir':str(data_root),'results_dir':str(run/'results'),'work_root':str(run/'active'),'default_timeout_sec':2400})+'\n')
 env=os.environ.copy(); env.update({'HARNESSBENCH_APP_CONFIG':str(app_cfg),'HARNESSBENCH_HARNESS_CONFIG':str(harness_cfg),'HARNESSBENCH_SKIP_PROCESS_GRADE':'1','HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM':'1','HARNESSBENCH_PUBLIC_URL_TEMPLATE':'{local_url}','PYTHONPATH':str(root/'src')})
 tasks=plan['tranches'][a.tranche]; results=results_root/MODEL_ID
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
  archive_completed_sandbox(matches[0],run)
  immutable_json(receipt,{**record,'status':'complete','finished_at':now(),'claim_file':str(claim),'claim_sha256':claim_hash})
 return 0
if __name__=='__main__': raise SystemExit(main())
