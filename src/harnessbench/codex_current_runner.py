from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from harnessbench.adapters.codex import codex_preflight
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


def _git_head(root: Path) -> str:
    completed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned, sequential, no-retry Codex 0.139 benchmark runner")
    parser.add_argument("--plan", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--execute", action="store_true", help="required to invoke Codex; omit for a dry run")
    parser.add_argument("--resume", action="store_true", help="skip terminal manifest entries; never retries them")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--manifest-dir", type=Path, default=Path("evaluation/runs") / NAMESPACE)
    parser.add_argument("--harness-config", type=Path, default=None)
    parser.add_argument("--app-config", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = load_app_config(args.app_config)
    models = load_model_config(args.harness_config)
    if NAMESPACE not in models:
        raise SystemExit(f"missing harness config namespace {NAMESPACE!r}")
    config = models[NAMESPACE]
    if config.get("adapter") != "codex":
        raise SystemExit(f"{NAMESPACE}: adapter must be 'codex'")
    tasks = load_tasks(app.tasks_dir)
    ordered = sorted(tasks, key=_sort_key)
    if len(ordered) != EXPECTED_TASK_COUNT:
        raise SystemExit(f"expected exactly {EXPECTED_TASK_COUNT} tasks, found {len(ordered)}")
    selected = list(SMOKE_TASKS if args.plan == "smoke" else ordered)
    missing = [task_id for task_id in selected if task_id not in tasks]
    if missing:
        raise SystemExit(f"plan contains missing tasks: {missing}")
    preflight = codex_preflight(config)
    plan = {"namespace": NAMESPACE, "plan": args.plan, "task_count": len(selected),
            "tasks": selected, "preflight": preflight, "execute": args.execute,
            "retry_policy": "one attempt per task; terminal failures are never retried"}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not preflight["ok"]:
        return 2
    if not args.execute:
        return 0

    run_dir = args.manifest_dir.expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not args.resume:
            raise SystemExit(f"manifest already exists: {manifest_path}; pass --resume")
        if manifest.get("namespace") != NAMESPACE or manifest.get("plan") != args.plan:
            raise SystemExit("existing manifest namespace/plan mismatch")
    else:
        manifest = {"schema_version": 1, "namespace": NAMESPACE, "plan": args.plan,
            "created_at": time.time(), "repository_head": _git_head(app.project_root),
            "task_snapshot": selected, "preflight": preflight, "entries": {}}
        _atomic_json(manifest_path, manifest)
    entries: dict[str, Any] = manifest.setdefault("entries", {})
    for index, task_id in enumerate(selected, 1):
        prior = entries.get(task_id)
        if prior:
            if prior.get("status") in {"succeeded", "failed"} and args.resume:
                print(f"[{index}/{len(selected)}] skip terminal {task_id}: {prior['status']}", flush=True)
                continue
            raise SystemExit(f"task {task_id} has non-terminal prior attempt; refusing an implicit retry")
        entry = {"status": "started", "attempt": 1, "started_at": time.time(), "index": index}
        entries[task_id] = entry; _atomic_json(manifest_path, manifest)
        raw_log = run_dir / "task-logs" / f"{task_id}.runner.log"
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = run_task(app, tasks[task_id], NAMESPACE, config, "live", keep_workspace=True)
            entry.update(status="succeeded" if result.adapter_result.ok else "failed",
                finished_at=time.time(), elapsed_sec=result.elapsed_sec, sandbox=str(result.sandbox),
                api_model_label=result.api_model_label, usage_summary=result.usage_summary,
                adapter_ok=result.adapter_result.ok,
                adapter_returncode=result.adapter_result.metadata.get("returncode"),
                oracle_result=result.oracle_result, scoring=result.scoring)
            raw_log.write_text(result.adapter_result.stdout + "\n--- STDERR ---\n" + result.adapter_result.stderr, encoding="utf-8")
        except KeyboardInterrupt:
            entry.update(status="interrupted", finished_at=time.time())
            _atomic_json(manifest_path, manifest); raise
        except Exception as exc:
            entry.update(status="failed", finished_at=time.time(), error_type=type(exc).__name__, error=str(exc))
            raw_log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        _atomic_json(manifest_path, manifest)
        if entry["status"] != "succeeded" and not args.continue_on_failure:
            print(f"stopping after {task_id}; failure is recorded and will not be retried", flush=True)
            return 1
    return 1 if any(entries.get(task_id, {}).get("status") != "succeeded" for task_id in selected) else 0


if __name__ == "__main__":
    raise SystemExit(main())
