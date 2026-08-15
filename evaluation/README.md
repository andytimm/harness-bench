# Prime Agent evaluation helpers

The pilot task list was committed in `NOTES.md` before benchmark results were observed and is duplicated as a constant in `run_prime_pilot.py`.

From the repository root, after installing the project into `.venv`:

```bash
PYTHONPATH=src .venv/bin/python evaluation/run_prime_pilot.py
```

The script runs tasks sequentially, skips paid/LLM process and oracle-quality grading, refuses to overwrite existing results by default, and writes a local run manifest under `reports/` (ignored by Git). Raw results and sandboxes use `config/app.yaml`.
