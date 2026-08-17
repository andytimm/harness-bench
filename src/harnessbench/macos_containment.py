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


def benchmark_integrity_paths(root: Path) -> list[Path]:
    """Minimal current-checkout roots whose mutation changes the evaluation."""
    root=root.resolve(); names=(".git","tasks","grading","evaluation","src","config")
    paths=[(root/name).resolve() for name in names]
    if not all(path.exists() or path.is_symlink() for path in paths):
        raise RuntimeError("benchmark integrity deny set is incomplete")
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


def _prefix_minimize(paths: Iterable[Path]) -> list[Path]:
    """Drop paths already covered by an ancestor deny."""
    result: list[Path] = []
    for path in sorted({p.resolve() for p in paths}, key=lambda p: (len(p.parts), str(p))):
        if not any(path == parent or _within(path, parent) for parent in result):
            result.append(path)
    return sorted(result, key=str)


def _runtime_capabilities(root: Path, workspace: Path, *, binary: Path | None,
                          capability_paths: list[Path] | None) -> list[Path]:
    visible_venv=root/".venv"; runtime_root=(visible_venv/"bin"/"python").resolve().parents[1]
    node=Path(shutil.which("node") or "").resolve()
    caps=[workspace.resolve(),visible_venv.resolve(),runtime_root]
    if binary is not None: caps.append(binary.resolve())
    if node.is_file(): caps.extend([node,node.parent])
    caps.extend(p.resolve() for p in (capability_paths or []))
    return sorted(set(caps),key=str)


def canonical_native_deny_paths(root: Path, auth_path: Path, *, workspace: Path,
                                control_paths: list[Path] | None = None) -> list[Path]:
    """The sole prefix-minimized benchmark, control-plane, and auth frontier."""
    root=root.resolve(); workspace=workspace.resolve(); auth_path=auth_path.resolve()
    controls=[]
    for control in (control_paths or []):
        resolved=control.resolve()
        controls.extend(_frontier(resolved,[workspace]) if _within(workspace,resolved) else [resolved])
    home=Path.home()
    auth_roots=[auth_path,home/".harnessbench",home/".claude",home/".claude.json",
                home/"Library"/"Keychains",home/".ssh",home/".aws",home/".config",
                home/".codex",home/".hermes"/"auth.json",home/".hermes"/".env",
                home/".hermes"/"config.yaml",home/".prime",Path("/usr/bin/security"),
                Path("/System/Library/Frameworks/Security.framework")]
    return _prefix_minimize([*repository_control_plane_paths(root),*other_worktrees(root),
                             *controls,*auth_roots])


def builtin_sensitive_paths(root: Path, auth_path: Path, *, workspace: Path,
                            control_paths: list[Path] | None = None) -> list[str]:
    """Compatibility view of the canonical native deny frontier."""
    return [str(p) for p in canonical_native_deny_paths(root,auth_path,workspace=workspace,
                                                        control_paths=control_paths)]



def native_credential_paths(auth_path: Path, *, workspace: Path,
                            control_paths: list[Path] | None = None) -> list[str]:
    """No second file-path layer: filesystem denyRead/denyWrite is canonical."""
    return []


def native_sandbox_policy(root: Path, auth_path: Path, *, workspace: Path, sandbox: Path,
                          binary: Path | None = None, capability_paths: list[Path] | None = None,
                          control_paths: list[Path] | None = None) -> dict:
    """Build a prefix-minimal native policy for Claude 2.1.227.

    Only reviewed auth/control-plane roots are denied. No deny overlaps an
    explicit workspace/runtime allow, avoiding Claude's recursive expansion of
    broad HOME denies into an E2BIG profile.
    """
    root=root.resolve(); workspace=workspace.resolve(); auth_path=auth_path.resolve()
    caps=_runtime_capabilities(root,workspace,binary=binary,capability_paths=capability_paths)
    deny=canonical_native_deny_paths(root,auth_path,workspace=workspace,
                                     control_paths=control_paths)
    home=Path.home()
    required=[auth_path.resolve(),(home/".claude").resolve(),(home/".claude.json").resolve(),
              (home/"Library"/"Keychains").resolve(),Path("/usr/bin/security").resolve(),
              Path("/System/Library/Frameworks/Security.framework").resolve()]
    if (not all(any(item == parent or _within(item,parent) for parent in deny) for item in required)
            or any(denied == cap or _within(cap,denied) or _within(denied,cap)
                   for denied in deny for cap in caps)):
        raise RuntimeError("native sandbox sensitive-path enumeration is incomplete or overlaps an allow")
    return {"allowRead":[str(p) for p in caps],"allowWrite":[str(workspace)],
            "denyRead":[str(p) for p in deny],"denyWrite":[str(p) for p in deny]}


# This bounds configured, post-prefix-minimization policy structure.  It is not
# an ARG_MAX proof; only the production-sized live Bash canary proves exec starts.
NATIVE_COMBINED_PREFIX_LIMIT = 40
NATIVE_FILESYSTEM_ENTRY_LIMIT = 80
NATIVE_POLICY_JSON_BUDGET = 64_000


def native_policy_metrics(policy: dict, credential_paths: Iterable[str | Path]) -> dict[str,int]:
    credentials=[str(x) for x in credential_paths]
    payload={"filesystem":policy,"credentials":{"files":credentials}}
    return {
        "unique_deny_prefixes":len(set(policy.get("denyRead",[]))|set(policy.get("denyWrite",[]))),
        "filesystem_deny_entries":len(policy.get("denyRead",[]))+len(policy.get("denyWrite",[])),
        "credential_file_entries":len(credentials),
        "policy_json_bytes":len(json.dumps(payload,separators=(",",":"),sort_keys=True).encode()),
    }


def validate_native_policy_shape(policy: dict, credential_paths: Iterable[str | Path],
                                 builtin_paths: Iterable[str | Path] = ()) -> bool:
    """Validate the one canonical, prefix-minimal native deny frontier."""
    try:
        allows=[Path(x).resolve() for x in [*policy["allowRead"],*policy["allowWrite"]]]
        read=[Path(x).resolve() for x in policy["denyRead"]]
        write=[Path(x).resolve() for x in policy["denyWrite"]]
        credentials=[Path(x).resolve() for x in credential_paths]
        expected=[Path(x).resolve() for x in builtin_paths]
    except (KeyError,TypeError):
        return False
    if (credentials or read != write or read != _prefix_minimize(read) or
            (expected and read != _prefix_minimize(expected))):
        return False
    home=Path.home().resolve()
    overlaps=any(denied == allowed or _within(allowed,denied) or _within(denied,allowed)
                 for denied in read for allowed in allows)
    metrics=native_policy_metrics(policy,credentials)
    return (not overlaps and home not in read and
            metrics["unique_deny_prefixes"] <= NATIVE_COMBINED_PREFIX_LIMIT and
            metrics["filesystem_deny_entries"] <= NATIVE_FILESYSTEM_ENTRY_LIMIT and
            metrics["policy_json_bytes"] <= NATIVE_POLICY_JSON_BUDGET)


def merged_policy_for_probe(policy: dict, credential_paths: Iterable[str | Path],
                            builtin_paths: Iterable[str | Path]) -> dict:
    """Return the already-canonical production filesystem policy."""
    if not validate_native_policy_shape(policy,credential_paths,builtin_paths):
        raise RuntimeError("merged Claude sandbox policy shape is unsafe")
    return {key:list(value) for key,value in policy.items()}





def _q(path: Path) -> str: return json.dumps(str(path.resolve()))
def native_seatbelt_profile(policy: dict) -> str:
    """Seatbelt probe with semantics equivalent to native allow precedence.

    Raw Seatbelt denies cannot be reopened. Expand each broad production deny
    into a frontier around explicit allows only for this offline probe.
    """
    def frontier(values: list[str], allows: list[str]) -> list[Path]:
        result=[]; caps=[Path(value).resolve() for value in allows]
        for value in values:
            denied=Path(value).resolve(); below=[cap for cap in caps if cap == denied or _within(cap,denied)]
            if below and denied.is_dir(): result.extend(_frontier(denied,below))
            elif denied not in below: result.append(denied)
        return _prefix_minimize(result)
    read=frontier(policy["denyRead"],policy["allowRead"])
    write=frontier(policy["denyWrite"],policy["allowWrite"])
    out=["(version 1)","(allow default)"]
    for value in read: out.append(f"(deny file-read* (subpath {_q(value)}))")
    for value in write: out.append(f"(deny file-write* (subpath {_q(value)}))")
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
