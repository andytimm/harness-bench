from __future__ import annotations
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('run_claude_full',ROOT/'evaluation/run_claude_full.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
from harnessbench.macos_containment import native_sandbox_policy, native_seatbelt_profile, verify_repo_containment, verify_task_capabilities, FILE_TOOLS, builtin_permission_denies, builtin_sensitive_paths, native_credential_paths, validate_builtin_permission_denies, validate_native_policy_shape
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
   self.assertIn(str(normal_state),sensitive); self.assertIn(str(denied.resolve()),sensitive); self.assertIn(str(keychains),credentials); self.assertIn(str(denied.resolve()),credentials); self.assertLess(len(credentials),12); self.assertTrue(validate_native_policy_shape(policy,credentials))
   rules=builtin_permission_denies(sensitive); self.assertTrue(validate_builtin_permission_denies(sensitive,rules))
   for path in (normal_state,keychains):
    for tool in FILE_TOOLS:
     self.assertIn(f'{tool}({path})',rules); self.assertIn(f'{tool}({path}/**)',rules)
   expected=[f'{tool}({path}{suffix})' for path in sorted(set(sensitive)) for tool in FILE_TOOLS for suffix in ('','/**')]
   self.assertEqual(rules,expected)
   overlapping={**policy,'denyRead':[str(workspace.parent)]}
   self.assertFalse(validate_native_policy_shape(overlapping,credentials))
   self.assertIn(str(ROOT/'.venv'),policy['allowRead'])
   self.assertIn(str((ROOT/'.venv/bin/python').resolve().parents[1]),policy['allowRead'])
  self.assertEqual(FILE_TOOLS,("Read","Edit","Write","Glob","Grep"))
 def test_production_shaped_native_profile_is_small_and_nonoverlapping(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
   run=Path(tmp); sandbox=run/'security-smoke'/'sandbox'; workspace=sandbox/'workspace'; workspace.mkdir(parents=True)
   private=sandbox/'private-evidence'; private.mkdir(); targets=[]
   for name in ('read','edit','write'):
    target=private/name; targets.append(target)
   (run/'plan.json').write_text('{}'); (run/'security-smoke'/'claim.json').write_text('{}'); (sandbox/'prompt.txt').write_text(''); (sandbox/'.claude-benchmark').mkdir()
   auth=run.parent/'.synthetic-dedicated-seed'
   policy=native_sandbox_policy(ROOT,auth,workspace=workspace,sandbox=sandbox,control_paths=[run,*targets])
   credentials=native_credential_paths(auth,workspace=workspace,control_paths=[run,*targets])
   self.assertTrue(validate_native_policy_shape(policy,credentials)); self.assertLess(len(policy['denyRead']),64); self.assertLess(len(native_seatbelt_profile(policy).encode()),128_000)
   for denied in map(Path,policy['denyRead']):
    for allowed in map(Path,policy['allowRead']): self.assertFalse(allowed==denied or allowed.is_relative_to(denied) or denied.is_relative_to(allowed))
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
 @unittest.skipUnless(sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').is_file() and shutil.which('claude'),'real macOS containment')
 def test_real_offline_containment_verifiers(self):
  with tempfile.TemporaryDirectory() as tmp:
   seed=Path(tmp)/'seed'; seed.mkdir(mode=0o700); verify_repo_containment(ROOT); verify_task_capabilities(ROOT,seed,binary=Path(shutil.which('claude')).resolve())
if __name__=='__main__': unittest.main()
