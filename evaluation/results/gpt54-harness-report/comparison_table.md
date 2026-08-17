# Harness-Bench comparison table

Raw outcome score; 106 tasks per option. Token fields follow each native source; components may overlap, so use Total for cross-harness comparison.

| Option | Score | Reported input | Reported cache read | Output | Total tokens | Calls | Runtime |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prime Agent | 86.65% | 1,387,583 | 3,663,872 | 314,356 | 5,365,811 | 670 | 1.87 h |
| Pi | 86.12% | 853,187 | 1,489,152 | 308,079 | 2,650,418 | 558 | 1.95 h |
| Hermes — aggressive* | 85.30% | 1,808,935 | 7,692,672 | 544,425 | 10,046,032 | 921 | 3.27 h |
| Hermes — focused* | 85.09% | 2,502,152 | 13,687,296 | 573,363 | 16,762,811 | 1,022 | 3.44 h |
| Hermes — default | 84.51% | 2,292,231 | 18,115,968 | 567,375 | 20,975,574 | 966 | 3.40 h |
| Codex CLI 0.139 | 80.26% | 15,304,660 | 13,760,384 | 505,527 | 15,810,187 | 649 | 2.92 h |

* Post-hoc fixed-profile ablation; do not pool with benchmark-default Hermes.

Excluded: original Hermes (oracle leakage); Claude Code Opus 4.6 (evaluation incomplete).
