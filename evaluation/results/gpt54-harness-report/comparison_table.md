# Harness-Bench comparison table

Raw outcome score; 106 tasks per option. Input is standardized as Total − Cache read − Output, so the three displayed token components are non-overlapping and sum to Total.

| Option | Score | Input tokens | Cache read tokens | Output tokens | Total tokens | Calls | Runtime |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prime Agent | 86.65% | 1,387,583 | 3,663,872 | 314,356 | 5,365,811 | 670 | 1.87 h |
| Pi | 86.12% | 853,187 | 1,489,152 | 308,079 | 2,650,418 | 558 | 1.95 h |
| Hermes — default | 84.51% | 2,292,231 | 18,115,968 | 567,375 | 20,975,574 | 966 | 3.40 h |
| Codex CLI 0.139 | 80.26% | 1,544,276 | 13,760,384 | 505,527 | 15,810,187 | 649 | 2.92 h |

Excluded: original Hermes (oracle leakage); Claude Code pilot (systemic tool failures; not a valid baseline).

## Hermes profile comparison

Contained default is the headline Hermes result. Focused/aggressive were a separate, counterbalanced post-hoc ablation.

| Hermes profile | Role | Score | Total tokens | Calls | Runtime |
|---|---|---:|---:|---:|---:|
| Hermes — default | Headline contained rerun | 84.51% | 20,975,574 | 966 | 3.40 h |
| Hermes — focused | Post-hoc: fixed skills surface | 85.09% | 16,762,811 | 1,022 | 3.44 h |
| Hermes — aggressive | Post-hoc: same surface minus skills | 85.30% | 10,046,032 | 921 | 3.27 h |

Aggressive minus focused: **+0.21 percentage points**; paired wins/ties/losses **15/67/24**; paired t-test **p=0.861**; task-bootstrap 95% CI approximately **[-2.12, +2.59] points**. No statistically detectable quality difference was observed in this single-run paired comparison, while aggressive used about 40% fewer tokens, 9.9% fewer calls, and 4.9% less runtime.
