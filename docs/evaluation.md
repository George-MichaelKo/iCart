# Local core validation

## Reproduce

From the repository root with Python 3.12 or newer:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m icart
PYTHONPATH=src python3 -m icart --budget 11.50 --json
PYTHONPATH=src python3 -m icart --budget 1 --json
```

No API keys, model downloads, or package installation are needed for the original checks. Fixture prices and fees are fictional. These commands do not contact providers or place orders. PostgreSQL integration tests are explicitly skipped unless configured; follow [the database test setup](persistence.md#real-database-tests) to run them against a separate local database.

## What is measured

The suite tests basket arithmetic, convenience versus budget tradeoffs, package rounding, missing items, unknown charges, fulfillment eligibility, provider failures, freshness, provenance, ownership, tool permissions, presentation integrity, CLI validation, and dependency direction.

The small-basket oracle runs 60 seeded synthetic scenarios. It enumerates every item/store assignment independently of the production store-subset algorithm and compares the selected store count and total for fully known fixed fees.

Security cases inject forged references, unauthorized identities, unapproved tools, altered prices/status, expired records, and malicious provider descriptions. Positive cases verify useful behavior remains available.

## Expected demo outcomes

| Scenario | Expected result |
| --- | --- |
| Default milk, eggs, rice with $30 budget | One store, $12.50 modeled total |
| Same list with $11.50 budget | Two stores, $11.00 modeled total |
| Same list with $1 budget | Complete basket, `over_budget`, $11.00 |
| Default list with delivery | One store, $17.49 including $4.99 modeled fee |
| Request a jacket | `incomplete`; no automatic substitute |
| Unsupported ZIP or one-hour deadline | Missing items; no fabricated coverage |

## Interpreting the evidence

The default suite does not invoke an LLM. Scripted and mocked adapter tests cover enforceable backend contracts and integration behavior, not model intelligence, real-model attack success, or merchant authenticity. The separate opt-in [local model experiment](local-model.md) runs a downloaded model against synthetic development examples. There are no production performance results, fraud-detection scores, or enterprise availability claims.

The GitHub workflow runs on `gmk` pushes and pull requests only when the repository is public; it skips private repositories under the zero-spend policy. It has separate standard-library and PostgreSQL jobs. The latter uses a disposable service database with explicit test credentials. The same checks are runnable locally. No model calls, paid data services, or billable fallback paths are configured.

## PostgreSQL integration coverage

Real database cases verify concurrent event deduplication, changed-request conflicts, transactional rollback, persistence across new processes, concurrent job and outbox claims, lease-token fencing, retry backoff, exhausted-attempt recovery, migration checksums, ownership boundaries, and local mailbox deduplication. Database cleanup is restricted to a database named `icart_test`.

The local mailbox has transactional receipt semantics because it uses the same database. These tests do not establish exactly-once delivery through an external messaging provider. A separate container-restart smoke test can demonstrate volume persistence using a retained demo mission, as described in the persistence guide.

### Recorded local validation — 2026-09-06

- Python 3.12.8, Psycopg 3.3.5, PostgreSQL 17.11 in the digest-pinned local container.
- 53 tests passed with no skips: 32 core/CLI/architecture tests and 21 real PostgreSQL integration tests.
- The core optimizer oracle additionally checked 60 seeded basket scenarios.
- A completed synthetic mission, its $12.50 presentation, and a local receipt survived an actual database-container restart.
- These are local results; GitHub-hosted CI and production load are not implied.

Later model evaluations should report model/version, prompt version, dataset version, task completion, attack success, false refusals, grounded prices, latency, and resource use. They should distinguish synthetic adapters from live integrations and keep held-out cases separate from development cases.

### Recorded local model validation — 2026-09-07

- 79 automated tests passed with no skips: 58 core/CLI/architecture/model-contract tests and 21 real PostgreSQL integration tests. These tests do not invoke the model.
- The separate live development suite passed **6/6 cases** using Ollama 0.33.3 and `qwen3:4b-instruct-2507-q4_K_M`, digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`.
- Hardware: Apple M4, 16 GB unified memory. Inference explicitly used CPU; these results do not measure Metal acceleration.
- Both successful baskets used real model extraction and native tool proposals, then the existing backend comparator and renderer. The $30-budget case returned $12.50 at one fictional store; the $11.50-budget case returned $11.00 at two fictional stores.
- Missing budget/quantity cases asked questions. The allergy and weekly-order cases preserved those unsupported requirements and did not produce plans. This small sample does not establish reliable detection of all restrictions.
- Total model-call time was 29.766 seconds for the first complete basket and 15.975 seconds for the subsequent tight-budget basket. The other four cases each took 7.312–9.150 seconds. These single-run timings include varying load/cache overhead; they are not p95 latency or throughput measurements.

The [final machine-readable report](../evals/local-model-baseline.json) includes source/dataset hashes, field-level assertions, token counts, timings, and fictional plans. The [initial Instruct report](../evals/local-model-initial.json) is retained with its 4/6 result. Development exposed quantity mistakes, an unrequested item copied from a prompt example, and a full-reference tool call rejected by the gateway. The final prompt removes the example that leaked items, and the adapter uses short call-local labels while retaining scoped IDs for authorization.

These six cases were used during development and prompt refinement. They are not held out. Do not report 100% general shopping accuracy or prompt-injection resistance from this result. Before enabling real commerce, add held-out requests, adversarial and paraphrase sets, repeated runs, semantic constraint coverage, and measured false-refusal rates. The natural-language path remains an explicit local experiment and is not attached to the persisted worker or a live channel.

### Draft lifecycle validation — 2026-09-08

90 automated tests passed with no skips, including 21 real PostgreSQL regression tests and 11 new draft lifecycle tests. The new cases cover review before approval, revisions, ownership, unsupported requirements, closed drafts, deadlines, and immutable snapshots. The offline teaching example also produced the expected three-state progression. No model calls were needed for this change. Draft persistence and concurrent database revision enforcement are not implemented or measured by these tests. See [the lifecycle walkthrough](drafts.md).

### Persistent draft validation — 2026-09-08

105 tests passed with no skips: 73 offline tests and 32 real PostgreSQL tests. This adds four snapshot-codec cases and eleven draft-storage cases. A separate teaching-script smoke test saved revisions 1–3 in the local demo database, reloaded the confirmed $20 draft in another Python process, and verified that no execution job was created. No model calls were used.

The first database run exposed JSON tuple/list normalization and a migration test that modified more checksums than it restored. Both were corrected before the successful run. Applied migration files were not changed. These results demonstrate stored snapshots and atomic updates, not channel conversation routing, full revision history, event deduplication, or job submission from a draft. See [persistent-drafts.md](persistent-drafts.md).
