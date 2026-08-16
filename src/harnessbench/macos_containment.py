from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONTAINMENT_CONTRACT = "macos-sandbox-exec-deny-control-plane-auth-readonly-checkout"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def repository_control_plane_paths(root: Path) -> list[Path]:
    # Deny every current checkout entry except the benchmark's read-only venv.
    root = root.resolve()
    venv = root / ".venv"
    if not (venv / "bin" / "python").is_file() or not (venv / "bin" / "pytest").is_file():
        raise RuntimeError("benchmark .venv Python/pytest toolchain is unavailable")
    paths = sorted((entry.resolve() for entry in root.iterdir() if entry.name != ".venv"), key=str)
    required = [root / name for name in ("tasks", "src", "tests", "config", "grading", "evaluation", ".git")]
    if any(not any(path == item.resolve() for path in paths) for item in required):
        raise RuntimeError("repository control-plane deny set is incomplete")
    return paths


def _selector(path: Path) -> str:
    return "subpath" if path.is_dir() else "literal"


def seatbelt_profile(read_write_denies: list[Path], write_denies: list[Path]) -> str:
    clauses = ["(version 1)", "(allow default)"]
    for path in read_write_denies:
        path = path.resolve()
        clauses.append(
            f"(deny file-read* file-write* ({_selector(path)} {json.dumps(str(path))}))"
        )
    for path in write_denies:
        path = path.resolve()
        clauses.append(f"(deny file-write* ({_selector(path)} {json.dumps(str(path))}))")
    return "\n".join(clauses) + "\n"


def containment_paths(root: Path, auth_path: Path | None = None) -> tuple[list[Path], list[Path]]:
    read_write = repository_control_plane_paths(root)
    if auth_path is not None:
        read_write.append(auth_path.resolve())
    # Complete checkout is read-only; reads are denied for every entry except .venv.
    return read_write, [root.resolve()]


def _sandbox_exec() -> Path:
    executable = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not executable.is_file():
        raise RuntimeError("reviewed macOS sandbox-exec containment is unavailable")
    return executable


def verify_repo_containment(root: Path) -> None:
    read_write, write_only = containment_paths(root)
    profile = seatbelt_profile(read_write, write_only)
    probe = root / "tasks" / "001-file" / "oracle_grade.py"
    completed = subprocess.run(
        [str(_sandbox_exec()), "-p", profile, "/bin/sh", "-c",
         'exec /bin/cat "$1"', "containment-probe", str(probe)],
        text=True, capture_output=True, timeout=10, check=False,
    )
    if completed.returncode == 0:
        raise RuntimeError("repo containment descendant unexpectedly read benchmark oracle data")


def verify_task_capabilities(root: Path, auth_path: Path) -> None:
    # Realistic offline smoke: task tools/resources work while control plane stays denied.
    root = root.resolve()
    auth_path = auth_path.resolve()
    read_write, write_only = containment_paths(root, auth_path)
    profile = seatbelt_profile(read_write, write_only)
    with tempfile.TemporaryDirectory(prefix="harnessbench-contained-smoke-") as temporary:
        workspace = Path(temporary)
        (workspace / "in").mkdir()
        (workspace / "out").mkdir()
        (workspace / "in" / "input.txt").write_text("fixture-data\n", encoding="utf-8")
        (workspace / "in" / "image.png").write_bytes(
            bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de")
        )
        (workspace / "test_smoke.py").write_text(
            "def test_workspace_fixture():\n"
            "    from pathlib import Path\n"
            "    assert Path('in/input.txt').read_text() == 'fixture-data\\n'\n",
            encoding="utf-8",
        )
        handler = partial(_QuietHandler, directory=str(workspace))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/in/input.txt"
            script = r'''set -eu
[ "$(cat in/input.txt)" = "fixture-data" ]
printf 'workspace-write
' > out/result.txt
"$5" -c 'from pathlib import Path; import subprocess; assert Path("in/image.png").read_bytes().startswith(b"\x89PNG"); assert subprocess.run(["/bin/echo", "child"], capture_output=True, text=True, check=True).stdout.strip() == "child"'
"$6" -q test_smoke.py
node --version >/dev/null
"$5" -c 'import sys, urllib.request; assert urllib.request.urlopen(sys.argv[1], timeout=5).read() == b"fixture-data\n"' "$1"
if /bin/cat "$2" >/dev/null 2>&1; then exit 91; fi
if /bin/cat "$3" >/dev/null 2>&1; then exit 92; fi
if /usr/bin/touch "$4" >/dev/null 2>&1; then exit 93; fi
'''
            env = os.environ.copy()
            env.pop("VIRTUAL_ENV", None)
            env.pop("PYTHONPATH", None)
            completed = subprocess.run(
                [str(_sandbox_exec()), "-p", profile, "/bin/sh", "-c", script,
                 "capability-smoke", url,
                 str(root / "tasks" / "079-smallfile-batch-reject-ledger" / "oracle_grade.py"),
                 str(auth_path), str(root / ".containment-write-probe"),
                 str(root / ".venv" / "bin" / "python"), str(root / ".venv" / "bin" / "pytest")],
                cwd=workspace, env=env, text=True, capture_output=True, timeout=30, check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if completed.returncode != 0:
            raise RuntimeError(
                "contained task-capability smoke failed "
                f"(rc={completed.returncode}): {completed.stdout[-1000:]} {completed.stderr[-1000:]}"
            )
        if (root / ".containment-write-probe").exists():
            raise RuntimeError("containment smoke unexpectedly wrote into benchmark checkout")
