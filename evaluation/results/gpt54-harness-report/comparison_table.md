# Harness-Bench comparison table

Combined score is computed per task as outcome × the unrounded mean of the three process-rubric dimensions × security, then rounded to four decimals using the evaluator’s Python float arithmetic. The separately stored process_score/process_effective field rounds that same mean to four decimals, so multiplying the displayed rounded components can differ by 0.0001. All four headline options use the same pinned Claude Sonnet 4.6 judge and complete, untruncated traces. Input is standardized as Total − Cache read − Output.

| Option | Combined | Outcome | Process | Security | Input tokens | Cache read tokens | Output tokens | Total tokens | Calls | Runtime |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Prime Agent | 82.96% | 86.07% | 96.27% | 100% | 1,387,583 | 3,663,872 | 314,356 | 5,365,811 | 670 | 1.87 h |
| Pi | 82.48% | 85.57% | 96.33% | 100% | 853,187 | 1,489,152 | 308,079 | 2,650,418 | 558 | 1.95 h |
| Hermes — default | 78.10% | 84.08% | 92.82% | 100% | 2,292,231 | 18,115,968 | 567,375 | 20,975,574 | 966 | 3.40 h |
| Codex CLI 0.139 | 73.40% | 80.04% | 90.17% | 100% | 1,544,276 | 13,760,384 | 505,527 | 15,810,187 | 649 | 2.92 h |

Combined is averaged after the taskwise multiplication; it is not the product of the displayed aggregate means. The Process column is aggregated from the exported unrounded rubric means; the normalized CSV also preserves the official four-decimal process_score. The outcome component includes the benchmark’s configured quality-LLM blend on tasks 008 and 013.

Excluded: original Hermes (oracle leakage); Claude Code pilot (systemic tool failures; not a valid baseline).

## Hermes profile comparison

Contained default is the headline Hermes result. Focused/aggressive were a separate, counterbalanced post-hoc ablation and have not been process-graded, so this table retains their execution-time raw outcome scores.

| Hermes profile | Role | Raw outcome | Total tokens | Calls | Runtime |
|---|---|---:|---:|---:|---:|
| Hermes — default | Headline contained rerun | 84.51% | 20,975,574 | 966 | 3.40 h |
| Hermes — focused | Post-hoc: fixed skills surface | 85.09% | 16,762,811 | 1,022 | 3.44 h |
| Hermes — aggressive | Post-hoc: same surface minus skills | 85.30% | 10,046,032 | 921 | 3.27 h |

Aggressive minus focused: **+0.21 percentage points**; paired wins/ties/losses **15/67/24**; paired t-test **p=0.861**; task-bootstrap 95% CI approximately **[-2.12, +2.59] points**. No statistically detectable quality difference was observed in this single-run paired comparison, while aggressive used about 40% fewer tokens, 9.9% fewer calls, and 4.9% less runtime.
