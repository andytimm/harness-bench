from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# This is deliberately not an outer containment claim.  Claude itself is the
# parent (and OAuth principal); its native Bash sandbox is the only OS boundary.
CONTAINMENT_CONTRACT = "claude-native-macos-bash-sandbox-plus-builtin-permission-denies"
FILE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")
EXPOSED_TOOLS = (*FILE_TOOLS, "Bash")


def repository_control_plane_paths(root: Path) -> list[Path]:
    """Every checkout entry except the visible, read-only benchmark venv."""
    root = root.resolve(); venv = root / ".venv"
    if not (venv / "bin" / "python").is_file() or not (venv / "bin" / "pytest").is_file():
        raise RuntimeError("benchmark .venv Python/pytest toolchain is unavailable")
    paths = sorted((p.resolve() for p in root.iterdir() if p.name != ".venv"), key=str)
    required = [root / n for n in ("tasks", "src", "tests", "config", "grading", "evaluation", ".git")]
    if any(p.resolve() not in paths for p in required):
        raise RuntimeError("repository control-plane deny set is incomplete")
    return paths


def _within(path: Path, parent: Path) -> bool:
    try: path.relative_to(parent); return True
    except ValueError: return False


def _frontier(directory: Path, capabilities: Iterable[Path]) -> list[Path]:
    """Enumerate a deny frontier, descending only toward explicit capabilities."""
    directory = directory.resolve(); allowed = sorted({p.resolve() for p in capabilities}, key=str)
    if not directory.is_dir(): return []
    result: list[Path] = []
    for entry in directory.iterdir():
        resolved = entry.resolve()
        if any(resolved == cap or _within(resolved, cap) for cap in allowed):
            continue
        below = [cap for cap in allowed if _within(cap, resolved)]
        if below and resolved.is_dir(): result.extend(_frontier(resolved, below))
        else: result.append(resolved)
    return result


def other_worktrees(root: Path) -> list[Path]:
    completed = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                               text=True, capture_output=True, timeout=10, check=False)
    if completed.returncode: raise RuntimeError("cannot enumerate benchmark worktrees")
    paths = [Path(line[9:]).resolve() for line in completed.stdout.splitlines() if line.startswith("worktree ")]
    return sorted({p for p in paths if p != root.resolve()}, key=str)


def native_sandbox_policy(root: Path, auth_path: Path, *, workspace: Path, sandbox: Path,
                          binary: Path | None = None, capability_paths: list[Path] | None = None,
                          control_paths: list[Path] | None = None) -> dict:
    """Build the exact reviewed policy passed to Claude 2.1.227 flag settings.

    Read protection is deny-by-enumeration because Claude's native allowRead
    takes precedence over denyRead.  No broad denied ancestor is re-opened.
    """
    root=root.resolve(); workspace=workspace.resolve(); sandbox=sandbox.resolve(); auth_path=auth_path.resolve()
    visible_venv=root/".venv"; visible_python=visible_venv/"bin"/"python"
    runtime_python=visible_python.resolve(); runtime_root=runtime_python.parents[1]
    node=Path(shutil.which("node") or "").resolve()
    caps=[workspace,sandbox,visible_venv,runtime_root]
    if binary is not None: caps.append(binary.resolve())
    if node.is_file(): caps.extend([node,node.parent])
    caps.extend(p.resolve() for p in (capability_paths or []))
    # Enumerate HOME recursively around capability roots, plus every checkout
    # control-plane entry and every sibling worktree.  Explicit high-value
    # paths are retained even if an ancestor frontier entry already covers them.
    home_denies=_frontier(Path.home(),caps)
    control_denies=[]
    for control in (control_paths or []):
        resolved=control.resolve()
        below=[cap for cap in caps if cap==resolved or _within(cap,resolved)]
        control_denies.extend(_frontier(resolved,below) if below and resolved.is_dir() else [resolved])
    normal_state=Path.home()/".claude.json"
    normal_claude=Path.home()/".claude"
    normal_keychains=Path.home()/"Library"/"Keychains"
    always_deny={auth_path,normal_state.resolve(),normal_claude.resolve(),normal_keychains.resolve()}
    explicit=repository_control_plane_paths(root)+other_worktrees(root)+control_denies+[
        auth_path, normal_state, normal_claude, normal_keychains, Path.home()/".ssh",
        Path.home()/".aws", Path.home()/".config", Path("/usr/bin/security"),
        Path("/System/Library/Frameworks/Security.framework"),
    ]
    # The HOME frontier covers every host-home path except explicit task/runtime
    # capabilities. Retain these exact high-value paths even if absent so both
    # native Bash and every built-in file tool receive literal denies.
    deny=sorted({p.resolve() for p in home_denies+explicit
                 if p.resolve() in always_deny or p.exists() or p.is_symlink()},key=str)
    if (not always_deny.issubset(set(deny)) or
            not all(p.resolve() in deny for p in repository_control_plane_paths(root))):
        raise RuntimeError("native sandbox sensitive-path enumeration is incomplete")
    return {"allowRead":[str(visible_venv),str(runtime_root)],
            "allowWrite":[str(workspace)], "denyRead":[str(p) for p in deny],
            "denyWrite":[str(p) for p in deny]}


def builtin_permission_denies(paths: Iterable[str | Path]) -> list[str]:
    """Exact ordered Cartesian deny set: every tool × literal/subtree path."""
    rules=[]
    for path in sorted({str(Path(p).resolve()) for p in paths}):
        for tool in FILE_TOOLS:
            rules.extend([f"{tool}({path})", f"{tool}({path}/**)"])
    return rules


def validate_builtin_permission_denies(paths: Iterable[str | Path], rules: object) -> bool:
    return isinstance(rules, list) and rules == builtin_permission_denies(paths)


def _q(path: Path) -> str: return json.dumps(str(path.resolve()))
def native_seatbelt_profile(policy: dict) -> str:
    """Offline probe profile equivalent to the native filesystem policy.

    This is used only to test the policy without launching Claude or a model;
    production never wraps Claude in sandbox-exec.
    """
    out=["(version 1)","(allow default)"]
    for value in policy["denyRead"]: out.append(f"(deny file-read* (subpath {_q(Path(value))}))")
    for value in policy["denyWrite"]: out.append(f"(deny file-write* (subpath {_q(Path(value))}))")
    return "\n".join(out)+"\n"


def _sandbox_exec() -> Path:
    executable=Path("/usr/bin/sandbox-exec")
    if sys.platform!="darwin" or not executable.is_file():
        raise RuntimeError("reviewed Claude native macOS sandbox is unavailable")
    return executable


def verify_repo_containment(root: Path) -> None:
    # Structural preflight only; the real offline native probe is the security
    # smoke script.  Avoid a misleading, second outer Seatbelt here.
    repository_control_plane_paths(root); other_worktrees(root)


def verify_task_capabilities(root: Path, seed_namespace: Path, *, binary: Path | None = None) -> None:
    # Compatibility preflight used by tranche runner; proves policy construction
    # and venv/runtime visibility without nesting a sandbox around Claude.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="harnessbench-native-preflight-") as td:
        workspace=Path(td); policy=native_sandbox_policy(root,seed_namespace,workspace=workspace,
            sandbox=workspace,binary=binary)
        visible=root/".venv/bin/python"
        completed=subprocess.run([str(visible),"-m","pytest","--version"],text=True,
                                 capture_output=True,timeout=30,check=False)
        if completed.returncode or not completed.stdout.startswith("pytest"):
            raise RuntimeError("visible benchmark venv Python cannot execute pytest")
        if str(root/".venv") not in policy["allowRead"] or str(visible.resolve().parents[1]) not in policy["allowRead"]:
            raise RuntimeError("native sandbox venv/runtime read capabilities missing")
