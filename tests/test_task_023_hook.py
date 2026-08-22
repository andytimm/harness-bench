from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path


class Task023HookContractTests(unittest.TestCase):
    def test_loopback_form_contract_and_cleanup(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        hook_path = repository / "tasks" / "023-web-form-extraction" / "hooks.py"
        spec = importlib.util.spec_from_file_location("task_023_hooks_contract", hook_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            source = repository / "tasks" / "023-web-form-extraction" / "fixtures" / "in" / "www"
            target = workspace / "in" / "www"
            target.parent.mkdir(parents=True)
            __import__("shutil").copytree(source, target)
            state = module.prepare_runtime({"workspace": workspace})
            try:
                url = state["MOCK_FORM_URL"]
                self.assertTrue(url.startswith("http://127.0.0.1:"))
                homepage = urllib.request.urlopen(url, timeout=3).read().decode()
                self.assertIn("local-csrf-742", homepage)
                body = urllib.parse.urlencode({"csrf_token": "local-csrf-742", "request_source": "invoice_portal", "order_id": "A-1042", "region": "emea"}).encode()
                request = urllib.request.Request(urllib.parse.urljoin(url, "/lookup"), data=body)
                lookup = json.load(urllib.request.urlopen(request, timeout=3))
                self.assertEqual(lookup["marker"], "FORM_LOOKUP_OK")
                confirm = json.load(urllib.request.urlopen(urllib.parse.urljoin(url, lookup["confirm_url"]), timeout=3))
                self.assertEqual(confirm["marker"], "FORM_CONFIRM_OK")
                paths = (workspace / "out" / "form_access.log").read_text().splitlines()
                self.assertEqual(paths, ["/", "/lookup", "/confirm"])
            finally:
                module.cleanup_runtime({"workspace": workspace}, state)


if __name__ == "__main__":
    unittest.main()
