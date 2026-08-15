# Prime Agent + GPT-5.4 medium: complete 106-task suite

Run completion date: 2026-08-15  
Harness-Bench base commit: `1025086a446653702b80cfb48babbeec35db6b2c`  
Prime Agent version: `0.7.2`  
Prime invocation: subscription-authenticated `openai-codex/gpt-5.4`, thinking `medium`

## Method

The full suite reused 50 previously validated pilot/subset results and executed the remaining 56 tasks sequentially. The resumable runner preflighted every retained result, used the corrected project virtualenv and loopback mock-service environment, stopped on validation failure, and checkpointed atomically after every task. No task was rerun because of its outcome. Process grading and oracle-quality LLM grading were intentionally skipped; results are deterministic oracle outcomes rather than standardized combined/process scores.

## Aggregate

- Adapter completion: **106/106**.
- Mean oracle outcome: **0.8665**; median: **0.9015**.
- Perfect outcomes: **35/106**; **53/106** outcomes were at least 0.9; **4/106** were below 0.6.
- Retained task wall time: **6,742.584 seconds** total; mean **63.6s/task**; median **55.7s/task**.
- Native usage: **670 model calls** over **118 benchmark rounds**.
- Tokens: **1,387,583 uncached input**, **3,663,872 cache-read**, **314,356 output**, **5,365,811 total**. Mean **50,621/task**; median **42,012/task**.
- The final 56-task continuation itself took **3,114.965 seconds** wall-clock and retained 3,107.643 seconds of task time, with 2,317,172 tokens and 306 calls.

## Overall comparison: exact 106 tasks, GPT-5.4 model label

| Harness | Mean completion/outcome | Median | Perfect | ≥0.9 | <0.6 |
|---|---:|---:|---:|---:|---:|
| Prime Agent | 0.8665 | 0.9014 | 35 | 53 | 4 |
| Codex | 0.8650 | 0.9045 | 30 | 54 | 6 |
| NanoBot | 0.8506 | 0.8972 | 37 | 53 | 8 |
| Hermes | 0.8296 | 0.8882 | 31 | 50 | 10 |

Prime scored **0.8665**, compared with Codex **0.8650**, NanoBot **0.8506**, and Hermes **0.8296**. Prime versus Codex is a +0.0015 difference: 32 wins, 55 exact ties, and 19 losses. This is again effectively a tie in a single-trajectory comparison.

Two tasks (`008-image-recognize` and `013-image-edit`) normally blend a 90%-weight oracle-quality LLM judgment. Because that judgment was deliberately skipped for Prime, Prime’s reported 1.0 on those tasks checks only required artifact existence and is not definitionally equivalent to published completion. Excluding both tasks leaves 104 deterministic tasks: Prime **0.8639**, Codex **0.8624**, NanoBot **0.8477**, and Hermes **0.8263**, so the headline relationship is unchanged.

## Category comparison

| Category | Tasks | Prime | Codex | NanoBot | Hermes | Prime time/task | Prime tokens/task |
|---|---:|---:|---:|---:|---:|---:|---:|
| Data, BI & Finance Analytics | 14 | 0.8309 | 0.8459 | 0.8547 | 0.8098 | 67.4s | 57,351 |
| Knowledge, Evidence & Retrieval | 13 | 0.7825 | 0.7801 | 0.7912 | 0.7181 | 64.2s | 40,864 |
| Long-running Autonomy & State Adaptation | 11 | 0.8718 | 0.8553 | 0.8342 | 0.8120 | 84.8s | 55,435 |
| Office & Business Communication | 12 | 0.8465 | 0.8603 | 0.8720 | 0.8387 | 37.1s | 30,753 |
| SRE, DevOps & Release Ops | 7 | 0.8624 | 0.8561 | 0.8717 | 0.8609 | 61.4s | 48,396 |
| Software Engineering & Codebase Maintenance | 22 | 0.9310 | 0.9312 | 0.8828 | 0.8476 | 73.8s | 68,189 |
| Vertical Professional Workflows | 12 | 0.8154 | 0.8057 | 0.8700 | 0.8808 | 61.0s | 44,898 |
| Workspace, Tool Use & Multimodal Operations | 15 | 0.9329 | 0.9214 | 0.8202 | 0.8682 | 53.4s | 45,010 |

## What changed relative to the frozen 47-task subset

- The subset mean was 0.8873 and median 0.9800; the full-suite mean is 0.8665 and median 0.9015. This is a composition change, not a rerun regression.
- The 59 added tasks averaged 0.8499 with median 0.8696. They add office communication, knowledge/evidence retrieval, vertical workflows, SRE, and multimodal/tool-use work.
- Prime remained strongest in software engineering (0.9310) and workspace/tool operations (0.9329). The lowest category was knowledge/evidence retrieval (0.7825), followed by vertical workflows (0.8154) and data/BI (0.8309).
- Compared with Codex, Prime led in workspace/tool operations, long-running autonomy, vertical workflows, SRE, and knowledge retrieval; it trailed modestly in office communication and data/BI, while software was effectively identical.

## Validation

All 106 retained results have adapter success, expected provider/model, Prime `0.7.2`, final stop reason `stop`, readable native session and stdout/stderr logs, no proxy-trace errors, no retained staged credential, no known pytest/PyYAML dependency failure, and no conservative raw-log reference to ground-truth/oracle task paths. This is a strong artifact audit, not an OS-level noninterference proof.

## Per-task results

| Task | Category | Outcome | Time | Calls | Rounds | Total tokens |
|---|---|---:|---:|---:|---:|---:|
| `001-file` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 5.020s | 2 | 1 | 8,730 |
| `002-exec` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 6.023s | 2 | 1 | 9,394 |
| `003-browser` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 10.339s | 3 | 1 | 13,918 |
| `004-meeting-summary` | Office & Business Communication | 0.8889 | 16.544s | 3 | 1 | 16,267 |
| `005-email-triage` | Office & Business Communication | 1.0000 | 24.556s | 5 | 1 | 30,265 |
| `006-access-bilibili` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 18.857s | 7 | 1 | 38,439 |
| `007-session-memory` | Long-running Autonomy & State Adaptation | 1.0000 | 17.539s | 4 | 2 | 20,280 |
| `008-image-recognize` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 22.553s | 5 | 1 | 30,007 |
| `009-git-pr-merge` | Software Engineering & Codebase Maintenance | 1.0000 | 18.279s | 3 | 1 | 16,048 |
| `010-office-docs` | Office & Business Communication | 1.0000 | 49.604s | 11 | 1 | 69,411 |
| `011-code-debug` | Software Engineering & Codebase Maintenance | 0.8650 | 90.744s | 22 | 5 | 164,120 |
| `012-doc-synthesis` | Knowledge, Evidence & Retrieval | 0.7500 | 49.107s | 4 | 1 | 26,465 |
| `013-image-edit` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 110.804s | 13 | 1 | 105,883 |
| `014-task-decomposition` | Long-running Autonomy & State Adaptation | 0.7975 | 74.669s | 5 | 1 | 38,862 |
| `015-security-injection-defense` | Knowledge, Evidence & Retrieval | 0.7000 | 30.568s | 4 | 1 | 24,311 |
| `016-code-repair-pytest` | Software Engineering & Codebase Maintenance | 1.0000 | 24.191s | 6 | 1 | 37,935 |
| `017-db-doc-consistency` | Software Engineering & Codebase Maintenance | 1.0000 | 87.697s | 8 | 1 | 92,171 |
| `018-provider-failover-audit` | Software Engineering & Codebase Maintenance | 0.9093 | 135.314s | 13 | 1 | 128,864 |
| `019-incident-runbook-synthesis` | SRE, DevOps & Release Ops | 0.8100 | 104.233s | 11 | 1 | 93,274 |
| `020-archive-checksum` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 29.573s | 5 | 1 | 32,237 |
| `021-batch-rename-transform` | Workspace, Tool Use & Multimodal Operations | 0.8235 | 113.233s | 7 | 1 | 60,138 |
| `022-local-rest-api-summary` | Workspace, Tool Use & Multimodal Operations | 0.8696 | 79.960s | 8 | 1 | 57,653 |
| `023-web-form-extraction` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 31.384s | 7 | 1 | 38,974 |
| `024-calendar-scheduling-conflict` | Office & Business Communication | 0.7778 | 43.601s | 6 | 1 | 47,326 |
| `025-meeting-action-tracker` | Office & Business Communication | 0.9444 | 67.147s | 5 | 1 | 41,945 |
| `026-ppt-brief-generation` | Office & Business Communication | 1.0000 | 36.587s | 3 | 1 | 19,046 |
| `027-contract-summary-risk` | Office & Business Communication | 0.7143 | 37.087s | 3 | 1 | 18,979 |
| `028-email-thread-merge` | Office & Business Communication | 0.8182 | 40.603s | 5 | 1 | 31,764 |
| `029-expense-packet-review` | Office & Business Communication | 0.6154 | 47.191s | 5 | 1 | 30,811 |
| `030-word-revision-plan` | Office & Business Communication | 0.8750 | 26.064s | 4 | 1 | 22,900 |
| `031-cross-doc-citation-check` | Office & Business Communication | 0.6667 | 37.580s | 4 | 1 | 23,775 |
| `032-customer-followup-draft` | Office & Business Communication | 0.8571 | 18.547s | 3 | 1 | 16,546 |
| `033-offline-knowledge-qa` | Knowledge, Evidence & Retrieval | 0.7667 | 21.056s | 5 | 1 | 26,875 |
| `034-evidence-matrix-claims` | Knowledge, Evidence & Retrieval | 0.9600 | 21.056s | 5 | 1 | 27,432 |
| `035-conflicting-source-resolution` | Knowledge, Evidence & Retrieval | 0.8400 | 74.165s | 5 | 1 | 41,785 |
| `036-citation-consistency-audit` | Knowledge, Evidence & Retrieval | 0.7800 | 86.686s | 4 | 1 | 40,179 |
| `037-policy-clause-retrieval` | Knowledge, Evidence & Retrieval | 0.7400 | 104.876s | 5 | 1 | 52,059 |
| `038-research-brief-synthesis` | Knowledge, Evidence & Retrieval | 0.9412 | 81.805s | 6 | 1 | 50,607 |
| `039-repo-architecture-map` | Software Engineering & Codebase Maintenance | 0.9785 | 128.953s | 5 | 1 | 52,430 |
| `040-test-coverage-fill` | Software Engineering & Codebase Maintenance | 1.0000 | 59.331s | 7 | 1 | 53,064 |
| `041-frontend-state-bug` | Software Engineering & Codebase Maintenance | 0.9812 | 72.791s | 4 | 1 | 32,012 |
| `042-api-schema-migration` | Software Engineering & Codebase Maintenance | 0.7400 | 128.371s | 6 | 1 | 68,150 |
| `043-db-migration-safety` | Software Engineering & Codebase Maintenance | 0.9950 | 178.358s | 12 | 1 | 177,631 |
| `044-ci-config-repair` | Software Engineering & Codebase Maintenance | 1.0000 | 40.203s | 7 | 1 | 44,377 |
| `045-dependency-upgrade-compat` | Software Engineering & Codebase Maintenance | 0.9800 | 59.236s | 7 | 1 | 51,222 |
| `046-performance-regression` | Software Engineering & Codebase Maintenance | 1.0000 | 33.765s | 5 | 1 | 27,039 |
| `047-code-review-risk-report` | Software Engineering & Codebase Maintenance | 0.6150 | 48.112s | 3 | 1 | 21,716 |
| `048-release-note-changelog` | Software Engineering & Codebase Maintenance | 0.9842 | 88.181s | 9 | 1 | 87,880 |
| `049-excel-like-cleaning` | Data, BI & Finance Analytics | 1.0000 | 88.199s | 9 | 1 | 83,369 |
| `050-multitable-join-analysis` | Data, BI & Finance Analytics | 1.0000 | 90.291s | 5 | 1 | 51,506 |
| `051-sql-query-report` | Data, BI & Finance Analytics | 1.0000 | 46.174s | 6 | 1 | 42,519 |
| `052-metric-definition-audit` | Data, BI & Finance Analytics | 0.6900 | 44.626s | 6 | 1 | 43,208 |
| `053-anomalous-transaction-detect` | Data, BI & Finance Analytics | 0.9786 | 43.604s | 6 | 1 | 47,182 |
| `054-budget-variance-analysis` | Data, BI & Finance Analytics | 1.0000 | 62.144s | 9 | 1 | 70,739 |
| `055-funnel-dropoff-analysis` | Data, BI & Finance Analytics | 0.9836 | 76.170s | 10 | 1 | 93,223 |
| `056-inventory-forecast` | Data, BI & Finance Analytics | 0.6900 | 34.580s | 7 | 1 | 46,298 |
| `057-interruption-resume` | Long-running Autonomy & State Adaptation | 0.7308 | 53.109s | 6 | 2 | 42,079 |
| `058-multiday-project-state` | Long-running Autonomy & State Adaptation | 0.8594 | 165.997s | 10 | 3 | 104,477 |
| `059-event-update-replan` | Long-running Autonomy & State Adaptation | 1.0000 | 62.740s | 6 | 2 | 43,494 |
| `060-task-cancellation-cleanup` | Long-running Autonomy & State Adaptation | 1.0000 | 44.161s | 6 | 2 | 39,302 |
| `061-periodic-status-rollup` | Long-running Autonomy & State Adaptation | 1.0000 | 83.300s | 5 | 1 | 34,030 |
| `062-k8s-config-audit` | SRE, DevOps & Release Ops | 0.8936 | 44.676s | 4 | 1 | 27,827 |
| `063-alert-dedup-noise` | SRE, DevOps & Release Ops | 0.9727 | 38.653s | 6 | 1 | 40,573 |
| `064-service-dependency-triage` | SRE, DevOps & Release Ops | 0.8222 | 52.703s | 4 | 1 | 32,845 |
| `065-capacity-planning` | SRE, DevOps & Release Ops | 0.6490 | 92.342s | 8 | 1 | 68,795 |
| `066-rollback-readiness` | SRE, DevOps & Release Ops | 0.8890 | 41.163s | 5 | 1 | 33,176 |
| `067-canary-release-check` | SRE, DevOps & Release Ops | 1.0000 | 56.211s | 5 | 1 | 42,281 |
| `068-product-launch-ops` | Vertical Professional Workflows | 1.0000 | 52.196s | 4 | 1 | 31,940 |
| `069-legal-compliance-review` | Vertical Professional Workflows | 0.8150 | 87.333s | 9 | 1 | 84,933 |
| `070-hr-resume-screening` | Vertical Professional Workflows | 0.7000 | 50.201s | 5 | 1 | 34,113 |
| `071-ecommerce-support-routing` | Vertical Professional Workflows | 0.8300 | 114.432s | 4 | 1 | 30,179 |
| `072-logistics-delay-response` | Vertical Professional Workflows | 0.8800 | 39.155s | 6 | 1 | 34,859 |
| `073-research-repro-package` | Vertical Professional Workflows | 0.4200 | 47.186s | 4 | 1 | 26,926 |
| `074-education-grading-feedback` | Vertical Professional Workflows | 0.6600 | 27.113s | 4 | 1 | 23,038 |
| `075-platform-appeal-review` | Vertical Professional Workflows | 0.9100 | 55.715s | 4 | 1 | 31,234 |
| `076-medical-admin-claim-check` | Vertical Professional Workflows | 0.8300 | 37.650s | 5 | 1 | 36,102 |
| `077-archive-manifest-defense` | Workspace, Tool Use & Multimodal Operations | 0.7241 | 84.326s | 5 | 1 | 45,816 |
| `078-local-api-cursor-retry-ledger` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 50.509s | 6 | 1 | 39,925 |
| `079-smallfile-batch-reject-ledger` | Workspace, Tool Use & Multimodal Operations | 0.5757 | 153.083s | 11 | 1 | 120,464 |
| `080-schema-roundtrip-conversion` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 55.710s | 5 | 1 | 39,296 |
| `081-local-html-dom-form-extract` | Workspace, Tool Use & Multimodal Operations | 1.0000 | 29.441s | 6 | 1 | 34,275 |
| `082-compose-config-repair` | Software Engineering & Codebase Maintenance | 0.9800 | 41.640s | 6 | 1 | 44,004 |
| `083-monorepo-interface-repair` | Software Engineering & Codebase Maintenance | 1.0000 | 44.809s | 10 | 1 | 74,803 |
| `084-js-state-type-bug` | Software Engineering & Codebase Maintenance | 0.9938 | 46.738s | 7 | 1 | 49,390 |
| `085-flaky-test-root-cause` | Software Engineering & Codebase Maintenance | 1.0000 | 55.217s | 11 | 1 | 81,523 |
| `086-sql-migration-preflight-rollback` | Software Engineering & Codebase Maintenance | 0.9784 | 84.324s | 4 | 1 | 34,678 |
| `087-cli-parser-bug-tests` | Software Engineering & Codebase Maintenance | 0.8821 | 58.924s | 8 | 1 | 65,116 |
| `088-api-contract-mock-client-compat` | Software Engineering & Codebase Maintenance | 0.6000 | 98.901s | 12 | 1 | 95,980 |
| `089-ab-test-caveat-analysis` | Data, BI & Finance Analytics | 0.9524 | 68.651s | 10 | 1 | 71,478 |
| `090-timeseries-anomaly-attribution` | Data, BI & Finance Analytics | 0.6271 | 61.651s | 6 | 1 | 50,498 |
| `091-financial-close-reconciliation` | Data, BI & Finance Analytics | 0.6393 | 79.193s | 7 | 1 | 61,681 |
| `092-schema-drift-audit` | Data, BI & Finance Analytics | 0.7400 | 107.228s | 4 | 1 | 42,547 |
| `093-jsonl-sessionization-analysis` | Data, BI & Finance Analytics | 0.5918 | 72.156s | 5 | 1 | 41,311 |
| `094-metric-definition-migration-diff` | Data, BI & Finance Analytics | 0.7391 | 69.154s | 7 | 1 | 57,349 |
| `095-policy-version-conflict-resolution` | Knowledge, Evidence & Retrieval | 0.7400 | 92.338s | 5 | 1 | 49,504 |
| `096-offline-knowledge-qa-insufficient-evidence` | Knowledge, Evidence & Retrieval | 0.6500 | 51.184s | 5 | 1 | 33,536 |
| `097-research-claims-batch-evidence-audit` | Knowledge, Evidence & Retrieval | 0.7400 | 113.753s | 9 | 1 | 82,194 |
| `098-three-source-decision-record-synthesis` | Knowledge, Evidence & Retrieval | 0.5644 | 61.134s | 5 | 1 | 38,420 |
| `099-privacy-dsar-intake-review` | Vertical Professional Workflows | 1.0000 | 103.718s | 10 | 1 | 104,543 |
| `100-financial-kyc-admin-check` | Vertical Professional Workflows | 0.7400 | 56.644s | 6 | 1 | 48,803 |
| `101-marketing-sensitive-commitment-review` | Vertical Professional Workflows | 1.0000 | 60.142s | 7 | 1 | 52,104 |
| `102-internal-doc-retrieval-injection-defense` | Knowledge, Evidence & Retrieval | 1.0000 | 47.107s | 5 | 1 | 37,870 |
| `103-policy-update-replan-diff` | Long-running Autonomy & State Adaptation | 0.9850 | 139.963s | 7 | 2 | 83,494 |
| `104-async-ops-window-rollup` | Long-running Autonomy & State Adaptation | 0.7975 | 134.365s | 11 | 1 | 99,071 |
| `105-partial-batch-resume-ledger` | Long-running Autonomy & State Adaptation | 0.7156 | 80.314s | 7 | 2 | 68,354 |
| `106-release-approval-gate-plan` | Long-running Autonomy & State Adaptation | 0.7045 | 76.300s | 4 | 1 | 36,341 |

## Provenance caveats

The published comparator is exact-task and exact model-label data from the authors’ public `leaderboard_scores.json`. It does not retain effort, backend revision, harness versions, benchmark SHA, or trajectories. The released Codex config uses medium effort, but run-level equivalence cannot be proven. Only completion/outcome is compared; Prime has no comparable process score.
