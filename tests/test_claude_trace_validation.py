from __future__ import annotations
import json, tempfile, unittest
from unittest import mock
from pathlib import Path
from harnessbench.adapters.claude_code import (_parse_stream, _parse_native_transcript, _normalize_trace,
    _quota_rejected, _keychain_service, _validate_seed, _validate_auth_status, _managed_policy_candidates, _unexpected_managed_policy, _open_lock)

SESSION="123e4567-e89b-12d3-a456-426614174000"
MODEL="claude-opus-4-6"
def realistic():
 return [
  {"type":"system","subtype":"init","session_id":SESSION,"model":MODEL},
  {"type":"assistant","session_id":SESSION,"uuid":"a","message":{"model":MODEL,"content":[{"type":"thinking","thinking":"x","signature":"sig"},{"type":"tool_use","id":"tool-1","name":"Read","input":{"file_path":"in/a"}}]}},
  {"type":"user","session_id":SESSION,"uuid":"b","message":{"content":[{"type":"tool_result","tool_use_id":"tool-1","content":"ok","is_error":False}]}},
  {"type":"assistant","session_id":SESSION,"uuid":"c","message":{"model":MODEL,"content":[{"type":"text","text":"done"}]}},
  {"type":"result","subtype":"success","is_error":False,"session_id":SESSION,"modelUsage":{MODEL:{"inputTokens":1,"outputTokens":2}}},
 ]
class TraceValidationTests(unittest.TestCase):
 def test_realistic_lossless_trace(self):
  rows=realistic(); parsed=_parse_stream("\n".join(json.dumps(x) for x in rows))
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/"trace.json"; _normalize_trace(parsed,target,native_session=SESSION,round_number=2,expected_model=MODEL)
   saved=json.loads(target.read_text()); self.assertEqual(saved["round"],2); self.assertEqual(saved["events"][1]["raw"],rows[1])
 def test_malformed_out_of_order_duplicate(self):
  with self.assertRaisesRegex(ValueError,"malformed"): _parse_stream('{')
  rows=realistic(); rows[0],rows[1]=rows[1],rows[0]
  with self.assertRaisesRegex(ValueError,"begin"): _parse_stream("\n".join(json.dumps(x) for x in rows))
  rows=realistic(); rows[2]["uuid"]="a"
  with self.assertRaisesRegex(ValueError,"duplicate"): _parse_stream("\n".join(json.dumps(x) for x in rows))
  rows=realistic(); rows.insert(-1,dict(rows[-1]))
  with self.assertRaisesRegex(ValueError,"terminal"): _parse_stream("\n".join(json.dumps(x) for x in rows))
 def test_orphan_tool_result_and_native_correlation(self):
  rows=realistic(); rows[2]["message"]["content"][0]["tool_use_id"]="missing"
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaisesRegex(ValueError,"orphan"): _normalize_trace(rows,Path(tmp)/"x",native_session=SESSION,round_number=1,expected_model=MODEL)
   native=Path(tmp)/"native.jsonl"; native.write_text(json.dumps({"uuid":"n1","sessionId":SESSION,"type":"assistant"})+'\n')
   self.assertEqual(len(_parse_native_transcript(native,SESSION)),1)
   native.write_text('{bad\n')
   with self.assertRaisesRegex(ValueError,"malformed native"): _parse_native_transcript(native,SESSION)
 def test_canonical_keychain_resolution_and_seed_modes(self):
  with tempfile.TemporaryDirectory() as tmp:
   seed=Path(tmp)/"seed"; seed.mkdir(mode=0o700)
   self.assertEqual(_validate_seed(seed),seed.resolve())
   self.assertFalse(any(seed.iterdir()))
   self.assertRegex(_keychain_service(seed),r"^Claude Code-credentials-[0-9a-f]{8}$")
   seed.chmod(0o755)
   with self.assertRaisesRegex(ValueError,"permissions"): _validate_seed(seed)
 def test_auth_status_is_strict_and_discards_identity(self):
  good={'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','subscriptionType':'max','email':'private@example.test'}
  completed=lambda value,code=0: mock.Mock(returncode=code,stdout=value,stderr='private')
  with mock.patch('harnessbench.adapters.claude_code.subprocess.run',return_value=completed(json.dumps(good))):
   self.assertEqual(_validate_auth_status(Path('/pinned/claude'),{'CLAUDE_CONFIG_DIR':'/seed','CLAUDE_SECURESTORAGE_CONFIG_DIR':'/seed'}),0)
  for value in ('not-json',json.dumps({**good,'loggedIn':False}),json.dumps({**good,'apiProvider':'thirdParty'}),json.dumps({**good,'subscriptionType':'free'})):
   with self.subTest(value=value), mock.patch('harnessbench.adapters.claude_code.subprocess.run',return_value=completed(value)):
    with self.assertRaises(ValueError): _validate_auth_status(Path('/pinned/claude'),{})
 def test_every_pinned_managed_policy_source_and_safe_lock(self):
  candidates={str(x) for x in _managed_policy_candidates('alice')}
  self.assertIn('/Library/Application Support/ClaudeCode/managed-settings.d',candidates)
  self.assertIn('/Library/Managed Preferences/alice/com.anthropic.claudecode.plist',candidates)
  self.assertIn('/Library/Managed Preferences/com.anthropic.claudecode.plist',candidates)
  self.assertIn('/etc/claude-code/managed-settings.d',candidates)
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'target'; target.write_text('x'); lock=Path(tmp)/'lock'; lock.symlink_to(target)
   with self.assertRaises((OSError,ValueError)): _open_lock(lock)
   policy=Path(tmp)/'managed-settings.d'; policy.mkdir(); (policy/'10-policy.json').write_text('{}')
   with mock.patch('harnessbench.adapters.claude_code._managed_policy_candidates',return_value=[policy]):
    found=_unexpected_managed_policy(); self.assertIn(str(policy),found); self.assertIn(str(policy/'10-policy.json'),found)
 def test_realistic_quota_events(self):
  self.assertFalse(_quota_rejected([{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1770000000}}]))
  self.assertTrue(_quota_rejected([{"type":"rate_limit_event","subtype":"rejected","rate_limit_info":{"status":"rejected","utilization":1.0}}]))
if __name__=='__main__': unittest.main()
