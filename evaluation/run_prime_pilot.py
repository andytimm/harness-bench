#!/usr/bin/env python3
"""Run the precommitted Prime Agent pilot tasks sequentially."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from harnessbench.config import load_app_config


PILOT_TASKS = [
    "016-code-repair-pytest",
    "039-repo-architecture-map",
    "050-multitable-join-analysis",
    "022-local-rest-api-summary",
    "010-office-docs",
    "057-interruption-resume",
    "083-monorepo-interface-repair",
    "104-async-ops-window-rollup",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="prime-agent-gpt-5.4-medium")
    parser.add_argument("--mode", default="live")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--manifest", type=Path, default=Path("reports/prime-agent-pilot-run.json"))
    return parser.parse_args()


def existing_results(results_dir: Path, harness: str, task_id: str) -> list[Path]:
    return sorted((results_dir / harness).glob(f"*/{task_id}.json"))


def main() -> int:
    args = parse_args()
    app = load_app_config()
    prior = {task: existing_results(app.results_dir, args.harness, task) for task in PILOT_TASKS}
    conflicts = {task: paths for task, paths in prior.items() if paths}
    if conflicts and not args.allow_existing:
        rendered = "\n".join(f"  {task}: {', '.join(map(str, paths))}" for task, paths in conflicts.items())
        raise SystemExit(f"Refusing to overwrite existing pilot results; use --allow-existing explicitly:\n{rendered}")

    env = os.environ.copy()
    # Oracles invoke `python3`, `pytest`, and other console scripts by name.
    # Prepending this interpreter's bin directory reproduces an activated venv.
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    env["HARNESSBENCH_SKIP_PROCESS_GRADE"] = "1"
    env["HARNESSBENCH_SKIP_ORACLE_QUALITY_LLM"] = "1"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows: list[dict[str, object]] = []

    for task_id in PILOT_TASKS:
        command = [
            sys.executable,
            "-m",
            "harnessbench.cli",
            "run-task",
            "--task",
            task_id,
            "--harness",
            args.harness,
            "--mode",
            args.mode,
        ]
        print(f"\n[pilot] running {task_id}", flush=True)
        start = time.perf_counter()
        completed = subprocess.run(command, env=env, check=False)
        elapsed = round(time.perf_counter() - start, 3)
        paths = existing_results(app.results_dir, args.harness, task_id)
        rows.append(
            {
                "task_id": task_id,
                "returncode": completed.returncode,
                "elapsed_sec": elapsed,
                "result_files": [str(path) for path in paths],
            }
        )

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "harness": args.harness,
        "mode": args.mode,
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "process_grade_skipped": True,
        "oracle_quality_llm_skipped": True,
        "tasks": rows,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\n[pilot] manifest: {args.manifest}")
    return 0 if all(row["returncode"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
