from __future__ import annotations
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('run_claude_full',ROOT/'evaluation/run_claude_full.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
from harnessbench.macos_containment import native_sandbox_policy, native_seatbelt_profile, verify_repo_containment, verify_task_capabilities, FILE_TOOLS, builtin_sensitive_paths, merged_policy_for_probe, native_credential_paths, validate_native_policy_shape
from harnessbench.adapters.claude_code import EXPECTED_SHA256

class ClaudePlanTests(unittest.TestCase):
 def test_immutable_odd_even_plan(self):
  plan=module.build_plan(ROOT); self.assertEqual(plan['target_count'],106); self.assertEqual(len(plan['tranches']['1']),53); self.assertEqual(len(plan['tranches']['2']),53); self.assertTrue(all(module.task_number(x)%2 for x in plan['tranches']['1'])); self.assertTrue(all(not module.task_number(x)%2 for x in plan['tranches']['2'])); self.assertEqual(set(plan['tranches']['1'])|set(plan['tranches']['2']),set(sum(plan['tranches'].values(),[])))
 def test_dry_plan_is_offline_resumable_and_external(self):
  with tempfile.TemporaryDirectory() as tmp:
   cmd=[sys.executable,str(ROOT/'evaluation/run_claude_full.py'),'--run-root',tmp,'--tranche','1','--dry-plan']; first=subprocess.run(cmd,text=True,capture_output=True); second=subprocess.run(cmd,text=True,capture_output=True); self.assertEqual(first.returncode,0,first.stderr); self.assertEqual(second.returncode,0,second.stderr); self.assertEqual(json.loads((Path(tmp)/'plan.json').read_text())['sha256'],EXPECTED_SHA256)
   bad=subprocess.run([sys.executable,str(ROOT/'evaluation/run_claude_full.py'),'--run-root',str(ROOT/'inside'),'--tranche','1','--dry-plan'],text=True,capture_output=True); self.assertNotEqual(bad.returncode,0)
 def test_settings_schema_and_native_two_control_contract(self):
  source=(ROOT/'src/harnessbench/adapters/claude_code.py').read_text()
  self.assertIn('"failIfUnavailable": True',source); self.assertIn('"credentials": {',source)
  self.assertIn('"mode": "deny"',source); self.assertIn('"allowUnsandboxedCommands": False',source)
  self.assertIn('"allowedDomains": ["127.0.0.1", "localhost"]',source)
  self.assertIn('"strictAllowlist": True',source); self.assertNotIn('"allowLocalBinding": True',source)
  self.assertNotIn('[str(sandbox_exec), "-p", profile, *cmd]',source)
  with tempfile.TemporaryDirectory() as tmp:
   workspace=Path(tmp)/'workspace'; workspace.mkdir(); auth=Path(tmp)/'seed'; auth.mkdir(mode=0o700)
   control=Path(tmp)/'run'; prior=control/'results'/'prior.json'; prior.parent.mkdir(parents=True); prior.write_text('{}'); denied=Path(tmp)/'private'/'sentinel'; denied.parent.mkdir(); denied.write_text('safe')
   policy=native_sandbox_policy(ROOT,auth,workspace=workspace,sandbox=workspace,control_paths=[control,denied])
   profile=native_seatbelt_profile(policy)
   normal_state=Path.home()/'.claude.json'; keychains=Path.home()/'Library'/'Keychains'
   self.assertNotIn(f'(subpath \"{Path.home()}\")',profile); self.assertIn(str(auth.resolve()),profile); self.assertIn(str(control.resolve()),profile)
   self.assertLess(len(policy['denyRead']),64); self.assertIn(str(workspace.resolve()),policy['allowRead'])
   for denied_path in map(Path,policy['denyRead']):
    for allowed_path in map(Path,policy['allowRead']):
     self.assertFalse(allowed_path==denied_path or allowed_path.is_relative_to(denied_path) or denied_path.is_relative_to(allowed_path))
   sensitive=builtin_sensitive_paths(ROOT,auth,workspace=workspace,control_paths=[control,denied])
   credentials=native_credential_paths(auth,workspace=workspace,control_paths=[control,denied])
   self.assertIn(str(normal_state),sensitive); self.assertIn(str(denied.resolve()),sensitive); self.assertEqual(credentials,[]); self.assertIn(str(keychains),policy['denyRead']); self.assertTrue(validate_native_policy_shape(policy,[]))
   for protected in (Path.home()/'.hermes'/'.env',Path.home()/'.hermes'/'config.yaml',Path.home()/'.prime'):
    self.assertIn(str(protected.resolve()),sensitive); self.assertIn(str(protected.resolve()),policy['denyRead'])
   self.assertNotIn(str((Path.home()/'.hermes').resolve()),policy['denyRead'])
   self.assertEqual(policy['denyRead'],policy['denyWrite'])
   self.assertEqual(len(policy['denyRead']),len(set(policy['denyRead'])))
   overlapping={**policy,'denyRead':[str(workspace.parent)]}
   self.assertFalse(validate_native_policy_shape(overlapping,credentials,sensitive))
   self.assertFalse(validate_native_policy_shape({**policy,'denyWrite':policy['denyWrite'][:-1]},[],sensitive))
   self.assertFalse(validate_native_policy_shape(policy,[str(auth)],sensitive))
   self.assertFalse(validate_native_policy_shape({**policy,'denyRead':policy['denyRead']+[policy['denyRead'][0]]},[],sensitive))
   self.assertIn(str(ROOT/'.venv'),policy['allowRead'])
   self.assertIn(str((ROOT/'.venv/bin/python').resolve().parents[1]),policy['allowRead'])
  self.assertEqual(FILE_TOOLS,("Read","Edit","Write","Glob","Grep"))
 def test_production_shaped_policy_is_constant_after_53_archived_tasks(self):
  with tempfile.TemporaryDirectory(dir=Path.home(),prefix='hb-'+('x'*80)) as tmp:
   run=Path(tmp); active=run/'active'; archive=run/'archive'; archive.mkdir(); sandbox=active/'model'/'api'/('task-105-'+('y'*120)); workspace=sandbox/'workspace'; workspace.mkdir(parents=True)
   private=sandbox/'private-evidence'; private.mkdir(); targets=[private/name for name in ('read','edit','write')]
   control_plane=run/'control-plane'; (control_plane/'claims').mkdir(parents=True); (control_plane/'plan.json').write_text('{}')
   (sandbox/'.claude-benchmark').mkdir(); (sandbox/'usage-proxy').mkdir(); (sandbox/'prompt-round1.txt').write_text('task')
   controls=[control_plane,archive,sandbox/'.claude-benchmark',sandbox/'usage-proxy',sandbox/'prompt-round1.txt']
   from harnessbench.models import TaskSpec
   from harnessbench.tasks import load_hooks
   hook_task=TaskSpec(task_id='018-provider-failover-audit',title='hook',task_dir=ROOT/'tasks'/'018-provider-failover-audit')
   hook_state=load_hooks(hook_task).prepare_runtime({'task':hook_task,'sandbox':sandbox,'workspace':workspace})
   hook_caps=[Path(value) for key,value in hook_state.items() if key.endswith(('_FILE','_DIR','_PATH')) and Path(value).is_absolute()]
   auth=run.parent/'.harnessbench'/'synthetic-dedicated-seed'
   def shape():
    policy=native_sandbox_policy(ROOT,auth,workspace=workspace,sandbox=sandbox,control_paths=controls,capability_paths=hook_caps)
    credentials=native_credential_paths(auth,workspace=workspace,control_paths=controls)
    sensitive=builtin_sensitive_paths(ROOT,auth,workspace=workspace,control_paths=controls)
    merged=merged_policy_for_probe(policy,[],sensitive)
    return policy,credentials,sensitive,merged
   before=shape()
   for index in range(53):
    prior=archive/'work'/f'model-{index:02d}'/('z'*120)/'workspace'; prior.mkdir(parents=True); (prior/'prior.txt').write_text('prior')
   after=shape()
   self.assertEqual(tuple(len(x) for x in before[1:]),tuple(len(x) for x in after[1:]))
   policy,credentials,sensitive,merged=after
   self.assertTrue(validate_native_policy_shape(policy,[],sensitive))
   self.assertLessEqual(len(merged['denyRead']),40); self.assertEqual(credentials,[]); self.assertLess(len(json.dumps({'permissions':{'allow':[],'deny':[]},'sandbox':{'filesystem':policy,'credentials':{'files':[]}}}).encode()),64_000)
   self.assertIn(str(archive.resolve()),sensitive); self.assertFalse(any('model-52' in path for path in sensitive))
   for current_control in (sandbox/'.claude-benchmark',sandbox/'usage-proxy',sandbox/'prompt-round1.txt'):
    self.assertIn(str(current_control.resolve()),sensitive); self.assertIn(str(current_control.resolve()),policy['denyRead'])
   self.assertTrue(all(any(cap.resolve()==Path(path) or cap.resolve().is_relative_to(Path(path)) for path in policy['allowRead']) for cap in hook_caps))
   prior=(archive/'work'/'model-52'/('z'*120)/'workspace'/'prior.txt').resolve()
   self.assertTrue(any(prior.is_relative_to(Path(path)) for path in sensitive))
   self.assertFalse(any(workspace.resolve()==Path(path) or workspace.resolve().is_relative_to(Path(path)) for path in sensitive))
   self.assertFalse(any((ROOT/'.venv').resolve().is_relative_to(Path(path)) for path in merged['denyRead']))
   profile=native_seatbelt_profile(merged); self.assertLess(len(profile.encode()),128_000)
   if sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').is_file():
    completed=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,'/bin/sh','-c',f'printf ok > {workspace}/out.txt; cat {prior}'],text=True,capture_output=True)
    self.assertNotEqual(completed.returncode,0); self.assertEqual((workspace/'out.txt').read_text(),'ok')
 def test_completed_sandbox_archives_behind_stable_prefix_and_keeps_paths(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp); sandbox=run/'active'/'model'/'api'/'task'; workspace=sandbox/'workspace'; workspace.mkdir(parents=True); (workspace/'out.txt').write_text('ok')
   result=run/'result.json'; result.write_text(json.dumps({'sandbox':str(sandbox),'workspace':str(workspace)}))
   destination=module.archive_completed_sandbox(result,run)
   self.assertTrue(sandbox.is_symlink()); self.assertEqual(sandbox.resolve(),destination.resolve()); self.assertEqual((workspace/'out.txt').read_text(),'ok')
   self.assertTrue(destination.is_relative_to((run/'archive'/'work').resolve()))
   with self.assertRaisesRegex(RuntimeError,'unarchived'): module.archive_completed_sandbox(result,run)
 def test_archive_rejects_symlink_ancestors_and_leaf_collisions(self):
  for symlink_at in ('archive','work','model','api'):
   with self.subTest(symlink_at=symlink_at), tempfile.TemporaryDirectory() as tmp:
    run=Path(tmp); sandbox=run/'active'/'model'/'api'/'task'; (sandbox/'workspace').mkdir(parents=True); result=run/'result.json'; result.write_text(json.dumps({'sandbox':str(sandbox)}))
    outside=run/'outside'; outside.mkdir(); archive=run/'archive'
    if symlink_at=='archive': archive.symlink_to(outside,target_is_directory=True); work=archive/'work'
    elif symlink_at=='work': archive.mkdir(); work=archive/'work'; work.symlink_to(outside,target_is_directory=True)
    else:
     work=archive/'work'; work.mkdir(parents=True)
     if symlink_at=='model': (work/'model').symlink_to(outside,target_is_directory=True)
     else: (work/'model').mkdir(); (work/'model'/'api').symlink_to(outside,target_is_directory=True)
    with self.assertRaisesRegex(RuntimeError,'symlink or non-directory'): module.archive_completed_sandbox(result,run)
    self.assertTrue(sandbox.is_dir()); self.assertFalse(any(outside.iterdir()))
  for collision_kind in ('directory','file'):
   with self.subTest(collision=collision_kind), tempfile.TemporaryDirectory() as tmp:
    run=Path(tmp); sandbox=run/'active'/'model'/'api'/'task'; (sandbox/'workspace').mkdir(parents=True); result=run/'result.json'; result.write_text(json.dumps({'sandbox':str(sandbox)}))
    collision=run/'archive'/'work'/'model'/'api'/'task'; collision.parent.mkdir(parents=True)
    collision.mkdir() if collision_kind=='directory' else collision.write_text('occupied')
    with self.assertRaisesRegex(RuntimeError,'collision'): module.archive_completed_sandbox(result,run)
    self.assertTrue(sandbox.is_dir()); self.assertTrue(collision.exists())
 def test_archive_revalidation_rejects_ancestor_swap_before_rename(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp); sandbox=run/'active'/'model'/'api'/'task'; (sandbox/'workspace').mkdir(parents=True); result=run/'result.json'; result.write_text(json.dumps({'sandbox':str(sandbox)})); outside=run/'outside'; outside.mkdir()
   original=module._revalidate_archive_parent; calls=0
   def swap(run_arg,parent,dirfd):
    nonlocal calls; calls+=1
    if calls==2:
     parent.rmdir(); parent.symlink_to(outside,target_is_directory=True)
    return original(run_arg,parent,dirfd)
   with mock.patch.object(module,'_revalidate_archive_parent',side_effect=swap):
    with self.assertRaisesRegex(RuntimeError,'changed'): module.archive_completed_sandbox(result,run)
   self.assertTrue(sandbox.is_dir()); self.assertFalse(any(outside.iterdir()))
 def test_archive_symlink_failure_rolls_back_rename(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp); sandbox=run/'active'/'model'/'api'/'task'; (sandbox/'workspace').mkdir(parents=True); result=run/'result.json'; result.write_text(json.dumps({'sandbox':str(sandbox)}))
   with mock.patch.object(Path,'symlink_to',side_effect=OSError('synthetic link failure')):
    with self.assertRaisesRegex(OSError,'synthetic link failure'): module.archive_completed_sandbox(result,run)
   self.assertTrue(sandbox.is_dir()); self.assertFalse((run/'archive'/'work'/'model'/'api'/'task').exists())
 def test_plan_binds_canonical_namespace_keychain_and_binary(self):
  with tempfile.TemporaryDirectory() as tmp:
   seed=Path(tmp)/'café'; plan=module.build_plan(ROOT,seed)
   self.assertEqual(plan['canonical_benchmark_seed'],__import__('unicodedata').normalize('NFC',str(seed.resolve())))
   self.assertEqual(plan['canonical_config_namespace'],plan['canonical_benchmark_seed']); self.assertRegex(plan['keychain_service'],r'^Claude Code-credentials-[0-9a-f]{8}$')
   self.assertTrue(Path(plan['binary']).is_absolute()); self.assertEqual(module.sha(Path(plan['binary'])),plan['binary_sha256']); self.assertEqual(module.digest({k:v for k,v in plan.items() if k!='plan_digest'}),plan['plan_digest'])
 def test_paid_adapter_config_has_full_exact_plan_binding(self):
  plan=module.build_plan(ROOT); binding=module.plan_binding(plan); cfg=module.adapter_model_config(plan)
  self.assertEqual(cfg['evaluation_plan_binding'],binding)
  self.assertEqual(tuple(binding),tuple(__import__('harnessbench.adapters.claude_code',fromlist=['CLAUDE_PLAN_BINDING_KEYS']).CLAUDE_PLAN_BINDING_KEYS))
  self.assertEqual(cfg['canonical_benchmark_seed'],binding['canonical_benchmark_seed'])
  self.assertEqual(cfg['canonical_config_namespace'],binding['canonical_config_namespace'])
  self.assertEqual(cfg['keychain_service'],binding['keychain_service'])
 def test_old_smoke_approval_schema_is_incompatible(self):
  source=(ROOT/'evaluation/run_claude_full.py').read_text()
  self.assertIn("'schema':2",source)
  self.assertIn("claude-code-2.1.227-production-permission-canary-approval",source)
  self.assertNotIn("expected_approval={'schema':1",source)
 @unittest.skipUnless(sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').is_file() and shutil.which('claude'),'real macOS containment')
 def test_real_offline_containment_verifiers(self):
  with tempfile.TemporaryDirectory() as tmp:
   seed=Path(tmp)/'seed'; seed.mkdir(mode=0o700); verify_repo_containment(ROOT); verify_task_capabilities(ROOT,seed,binary=Path(shutil.which('claude')).resolve())
if __name__=='__main__': unittest.main()
