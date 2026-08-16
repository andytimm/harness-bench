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

CONTAINMENT_CONTRACT = "macos-seatbelt-home-deny-capability-allow-native-credential-deny"


class _DenyPaths(list[Path]):
    def __init__(self, values: list[Path], allow_read: list[Path] | None = None):
        super().__init__(values)
        self.allow_read = allow_read or []
        self.auth_display: Path | None = None



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


def _filter(selector: str, path: Path) -> str:
    return f"({selector} {json.dumps(str(path.resolve()))})"


def seatbelt_profile(read_write_denies: list[Path], write_denies: list[Path]) -> str:
    """Create a deny-by-capability profile for host/home data.

    Seatbelt deny rules win over allow rules, so exceptions are expressed as
    ``require-not`` filters within the broad home deny rather than later allows.
    """
    clauses = ["(version 1)", "(allow default)"]
    if getattr(read_write_denies, "auth_display", None) is not None:
        clauses.append("; canonical parent auth capability " + str(read_write_denies.auth_display))
    allowed = [p.resolve() for p in getattr(read_write_denies, "allow_read", [])]
    home = Path.home().resolve()
    if allowed:
        exceptions = " ".join(f"(require-not (subpath {json.dumps(str(p))}))" for p in allowed)
        clauses.append(f"(deny file-read* (subpath {json.dumps(str(home))}) {exceptions})")
    for path in read_write_denies:
        path = path.resolve()
        clauses.append(f"(deny file-read* file-write* ({_selector(path)} {json.dumps(str(path))}))")
    for path in write_denies:
        path = path.resolve()
        clauses.append(f"(deny file-write* ({_selector(path)} {json.dumps(str(path))}))")
    return "\n".join(clauses) + "\n"


def containment_paths(root: Path, auth_path: Path | None = None, *, workspace: Path | None = None,
                      sandbox: Path | None = None, binary: Path | None = None,
                      capability_paths: list[Path] | None = None) -> tuple[list[Path], list[Path]]:
    root = root.resolve()
    denied = repository_control_plane_paths(root)
    allowed = [root / ".venv"]
    for value in (workspace, sandbox, binary):
        if value is not None: allowed.append(value.resolve())
    allowed.extend(p.resolve() for p in (capability_paths or []))
    # The parent Claude process must read its one canonical OAuth namespace.
    # Native sandbox credentials + permissions deny that namespace to Read and
    # Bash descendants.  It must therefore be an exception to the outer profile.
    if auth_path is not None:
        candidate = auth_path if auth_path.is_dir() else auth_path.parent
        allowed.append(candidate.resolve())
    result = _DenyPaths(denied, allowed)
    result.auth_display = auth_path
    return result, [root]


def _capability_paths_from_env(env: dict[str, str]) -> list[Path]:
    paths = []
    for key, value in env.items():
        if key.endswith(("_FILE", "_DIR", "_PATH")) and value and Path(value).is_absolute():
            paths.append(Path(value))
    return paths


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
