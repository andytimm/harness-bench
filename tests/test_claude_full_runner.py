from __future__ import annotations
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('run_claude_full',ROOT/'evaluation/run_claude_full.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
from harnessbench.macos_containment import containment_paths, seatbelt_profile
from harnessbench.adapters.claude_code import EXPECTED_SHA256

class ClaudePlanTests(unittest.TestCase):
 def test_immutable_odd_even_plan(self):
  plan=module.build_plan(ROOT); self.assertEqual(plan['target_count'],106); self.assertEqual(len(plan['tranches']['1']),53); self.assertEqual(len(plan['tranches']['2']),53); self.assertTrue(all(module.task_number(x)%2 for x in plan['tranches']['1'])); self.assertTrue(all(not module.task_number(x)%2 for x in plan['tranches']['2'])); self.assertEqual(set(plan['tranches']['1'])|set(plan['tranches']['2']),set(sum(plan['tranches'].values(),[])))
 def test_dry_plan_is_offline_resumable_and_external(self):
  with tempfile.TemporaryDirectory() as tmp:
   cmd=[sys.executable,str(ROOT/'evaluation/run_claude_full.py'),'--run-root',tmp,'--tranche','1','--dry-plan']; first=subprocess.run(cmd,text=True,capture_output=True); second=subprocess.run(cmd,text=True,capture_output=True); self.assertEqual(first.returncode,0,first.stderr); self.assertEqual(second.returncode,0,second.stderr); self.assertEqual(json.loads((Path(tmp)/'plan.json').read_text())['sha256'],EXPECTED_SHA256)
   bad=subprocess.run([sys.executable,str(ROOT/'evaluation/run_claude_full.py'),'--run-root',str(ROOT/'inside'),'--tranche','1','--dry-plan'],text=True,capture_output=True); self.assertNotEqual(bad.returncode,0)
 def test_settings_schema_and_outer_profile_contract(self):
  source=(ROOT/'src/harnessbench/adapters/claude_code.py').read_text(); self.assertIn('"failIfUnavailable": True',source); self.assertIn('"credentials": {',source); self.assertIn('"mode": "deny"',source); self.assertIn('"/usr/bin/security"',source); self.assertIn('"allowUnsandboxedCommands": False',source)
  with tempfile.TemporaryDirectory() as tmp:
   auth=Path(tmp)/'auth'; auth.write_text('{}'); reads,writes=containment_paths(ROOT,auth); profile=seatbelt_profile(reads,writes); self.assertIn(str(ROOT/'tasks'),profile); self.assertIn(str(auth),profile); self.assertIn('(deny file-write* (subpath '+json.dumps(str(ROOT.resolve()))+'))',profile)
if __name__=='__main__': unittest.main()
