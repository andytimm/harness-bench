from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from harnessbench.adapters.codex import codex_preflight, provision_run_private_auth
from harnessbench.config import load_app_config, load_model_config
from harnessbench.runner import run_task
from harnessbench.tasks import load_tasks

NAMESPACE = "codex-gpt-5.4-medium-current-0.139"
EXPECTED_TASK_COUNT = 106
SMOKE_TASKS = ("001-file", "044-ci-config-repair")


def _sort_key(task_id: str) -> tuple[int, str]:
    prefix = task_id.split("-", 1)[0]
    return (int(prefix), task_id) if prefix.isdigit() else (10**9, task_id)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_state(root: Path) -> dict[str, Any]:
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"], capture_output=True, text=True, check=False)
    return {"head": head.stdout.strip() if head.returncode == 0 else "",
            "dirty": bool(status.stdout.strip()) or status.returncode != 0,
            "status_sha256": hashlib.sha256(status.stdout.encode()).hexdigest()}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file() and "__pycache__" not in item.parts):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode()); digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()



def _task_fingerprint(task: Any) -> dict[str, str]:
    directory = Path(task.task_dir)
    return {"tree_sha256": _tree_hash(directory),
            "spec_sha256": _file_hash(directory / "task.yaml"),
            "fixtures_sha256": _tree_hash(directory / task.fixtures_dir)}

def _binary_pin(preflight: dict[str, Any]) -> dict[str, str]:
    provenance = preflight["provenance"]
    return {key: str(provenance.get(key) or "") for key in
            ("version", "resolved", "sha256", "native_executable", "native_sha256")}


def _execution_config(config: dict[str, Any], binary: dict[str, str], private_auth_file: Path | None = None) -> dict[str, Any]:
    pinned = dict(config)
    if private_auth_file is not None:
        pinned["benchmark_auth_file"] = str(private_auth_file)
    pinned.update({"expected_resolved_executable": binary["resolved"],
                   "expected_executable_sha256": binary["sha256"],
                   "expected_native_executable": binary["native_executable"],
                   "expected_native_sha256": binary["native_sha256"]})
    return pinned


def _manifest_provenance(*, app: Any, config: dict[str, Any], ordered: list[str], selected: list[str], tasks: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    return {"repository": _git_state(app.project_root), "ordered_task_ids": ordered,
            "selected_task_ids": selected,
            "task_hashes": {task_id: _task_fingerprint(tasks[task_id]) for task_id in selected},
            "config_pins": {key: config[key] for key in sorted(config)},
            "auth_seed": {"path": preflight["benchmark_auth_file"],
                "sha256": _file_hash(Path(preflight["benchmark_auth_file"]))},
            "codex_pins": {"model": preflight["model"], "reasoning": preflight["reasoning"],
                "provider": preflight["provider"], "billing_mode": preflight["billing_mode"],
                "binary": _binary_pin(preflight)}}



def _manifest_dir_allowed(repository: Path, directory: Path) -> bool:
    resolved = directory.resolve()
    try:
        resolved.relative_to(repository.resolve())
    except ValueError:
        return True
    completed = subprocess.run(["git", "-C", str(repository), "check-ignore", "-q", str(resolved)],
                               capture_output=True, check=False)
    return completed.returncode == 0


def _acquire_run_lock(run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / "runner-exclusive.lock"
    if lock_path.is_symlink():
        raise SystemExit(f"refusing symlink runner lock: {lock_path}")
    handle = lock_path.open("a+", encoding="utf-8")
    if __import__("os").name != "posix":
        handle.close(); raise SystemExit("run-exclusive locks require POSIX")
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close(); raise SystemExit(f"another runner owns {run_dir}")
    return handle

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned, sequential, no-retry Codex 0.139 benchmark runner")
    parser.add_argument("--plan", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--execute", action="store_true", help="required to invoke Codex; omit for a dry run")
    parser.add_argument("--resume", action="store_true", help="skip terminal manifest entries; never retries them")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--manifest-dir", type=Path, default=None,
                        help="default: evaluation/runs/<namespace>/<smoke|full>")
    parser.add_argument("--harness-config", type=Path, default=None)
    parser.add_argument("--app-config", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = load_app_config(args.app_config); models = load_model_config(args.harness_config)
    if NAMESPACE not in models:
        raise SystemExit(f"missing harness config namespace {NAMESPACE!r}")
    config = models[NAMESPACE]
    if config.get("adapter") != "codex":
        raise SystemExit(f"{NAMESPACE}: adapter must be 'codex'")
    tasks = load_tasks(app.tasks_dir); ordered = sorted(tasks, key=_sort_key)
    if len(ordered) != EXPECTED_TASK_COUNT:
        raise SystemExit(f"expected exactly {EXPECTED_TASK_COUNT} tasks, found {len(ordered)}")
    selected = list(SMOKE_TASKS if args.plan == "smoke" else ordered)
    if any(task_id not in tasks for task_id in selected):
        raise SystemExit("plan contains missing tasks")
    preflight = codex_preflight(config)
    provenance = _manifest_provenance(app=app, config=config, ordered=ordered,
        selected=selected, tasks=tasks, preflight=preflight)
    plan = {"namespace": NAMESPACE, "plan": args.plan, "task_count": len(selected),
            "tasks": selected, "preflight": preflight, "provenance": provenance,
            "execute": args.execute,
            "retry_policy": "one attempt per task; terminal failures are never retried"}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not preflight["ok"]:
        return 2
    if not args.execute:
        return 0
    if provenance["repository"]["dirty"]:
        raise SystemExit("execution requires a clean repository so manifest provenance is stable")

    default_dir = Path("evaluation/runs") / NAMESPACE / args.plan
    run_dir = (args.manifest_dir or default_dir).expanduser().resolve()
    if not _manifest_dir_allowed(app.project_root, run_dir):
        raise SystemExit("manifest dir must be outside the repository or covered by git ignore rules")
    run_lock = _acquire_run_lock(run_dir)
    try:
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not args.resume:
                raise SystemExit(f"manifest already exists: {manifest_path}; pass --resume")
            if manifest.get("namespace") != NAMESPACE or manifest.get("plan") != args.plan:
                raise SystemExit("existing manifest namespace/plan mismatch")
            if manifest.get("provenance") != provenance:
                raise SystemExit("existing manifest provenance differs (repo/tasks/config/pins/order)")
        else:
            manifest = {"schema_version": 2, "namespace": NAMESPACE, "plan": args.plan,
                "created_at": time.time(), "provenance": provenance, "entries": {}}
            _atomic_json(manifest_path, manifest)

        binary = provenance["codex_pins"]["binary"]
        private_auth_file = run_dir / "private-codex-home" / "auth.json"
        auth_provision = provision_run_private_auth(Path(preflight["benchmark_auth_file"]), private_auth_file)
        expected_auth_meta = {"path": str(private_auth_file),
            "seed_sha256": auth_provision["seed_sha256"], "host_auth_written": False}
        existing_auth_meta = manifest.setdefault("run_private_auth", expected_auth_meta)
        if existing_auth_meta != expected_auth_meta:
            raise SystemExit("run-private auth manifest provenance differs")
        _atomic_json(manifest_path, manifest)
        task_config = _execution_config(config, binary, private_auth_file)
        entries: dict[str, Any] = manifest.setdefault("entries", {})
        for index, task_id in enumerate(selected, 1):
            # Enforce the manifest's launcher/native paths and digests immediately before every task.
            current = codex_preflight(task_config)
            if not current["ok"] or _binary_pin(current) != binary:
                raise SystemExit(f"Codex provenance/pins changed before task {task_id}")
            current_repo = _git_state(app.project_root)
            if current_repo != provenance["repository"]:
                raise SystemExit(f"repository changed before task {task_id}")
            if _task_fingerprint(tasks[task_id]) != provenance["task_hashes"][task_id]:
                raise SystemExit(f"task inputs changed before task {task_id}")
            prior = entries.get(task_id)
            if prior:
                if prior.get("status") in {"succeeded", "failed"} and args.resume:
                    print(f"[{index}/{len(selected)}] skip terminal {task_id}: {prior['status']}", flush=True)
                    continue
                raise SystemExit(f"task {task_id} has non-terminal prior attempt; refusing an implicit retry")
            entry = {"status":"started", "attempt":1, "started_at":time.time(), "index":index,
                     "task_hash":provenance["task_hashes"][task_id], "binary_pin":binary}
            entries[task_id] = entry; _atomic_json(manifest_path, manifest)
            raw_log = run_dir / "task-logs" / f"{task_id}.runner.log"; raw_log.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = run_task(app, tasks[task_id], NAMESPACE, task_config, "live", keep_workspace=True)
                entry.update(status="succeeded" if result.adapter_result.ok else "failed",
                    finished_at=time.time(), elapsed_sec=result.elapsed_sec, sandbox=str(result.sandbox),
                    api_model_label=result.api_model_label, usage_summary=result.usage_summary,
                    adapter_ok=result.adapter_result.ok,
                    adapter_returncode=result.adapter_result.metadata.get("returncode"),
                    executable_provenance=result.adapter_result.metadata.get("executable_provenance"),
                    oracle_result=result.oracle_result, scoring=result.scoring)
                raw_log.write_text(result.adapter_result.stdout + "\n--- STDERR ---\n" + result.adapter_result.stderr, encoding="utf-8")
            except KeyboardInterrupt:
                entry.update(status="interrupted", finished_at=time.time()); _atomic_json(manifest_path, manifest); raise
            except Exception as exc:
                entry.update(status="failed", finished_at=time.time(), error_type=type(exc).__name__, error=str(exc))
                raw_log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            _atomic_json(manifest_path, manifest)
            if entry["status"] != "succeeded" and not args.continue_on_failure:
                print(f"stopping after {task_id}; failure is recorded and will not be retried", flush=True); return 1
        return 1 if any(entries.get(task_id, {}).get("status") != "succeeded" for task_id in selected) else 0
    finally:
        run_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
