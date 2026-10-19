# Explaining iCart in an interview

## A concise introduction

> I am building a local-first commerce assistant around shopping missions rather than individual product searches. The implemented backend compares synthetic baskets with explicit budgets and quantities, enforces mission-scoped offer references, and uses PostgreSQL transactions and leased jobs for recovery. The architecture separates the commerce domain from model, storage, product, and channel adapters. An experimental local Ollama adapter interprets requests and proposes a comparison tool call. NeMo and iMessage remain planned integrations.

Use the current implementation status from the README when describing the project. Do not claim production traffic, real-model attack resistance, or live merchants without measured evidence.

## Tools versus ordinary application functions

A model tool is an operation the model may request. An ordinary function can be invoked only by application code. A database method is not automatically exposed to the model just because it exists.

**There is currently one model-accessible tool: `compare_baskets`.** Its only argument is the tuple of issued offer references. The scripted or optional local model returns that request; the gateway validates it before invoking the deterministic comparator.

| Operation | Invoked by | What it does | Why it is controlled |
| --- | --- | --- | --- |
| `ProductProvider.search` | Shopping service | Reads offers for a mission. | Provider identity, result size, location, and eligibility are checked. |
| `OfferRegistry.register` | Shopping service | Issues a random reference bound to user and mission. | The model cannot mint permissions or overwrite authoritative records. |
| `OfferRegistry.resolve` | Gateway and renderer | Loads a reference after ownership checks. | A guessed or cross-mission ID is insufficient. |
| `ModelAdapter.propose` | Shopping service | Returns a structured proposed tool call. | Both model paths get issued references only for this call, with no raw descriptions or private records. |
| `compare_baskets` | Gateway after validation | Computes package counts, fees, and candidate plan ranking. | The model cannot fabricate prices or remove inconvenient offers. |
| `render` | Interface/composed executor | Resolves records and checks totals and coverage before display. | Presentation uses authoritative values rather than model-written prices. |
| `WorkflowStore.submit` | Local CLI; future authenticated ingress | Saves a mission and its job atomically. | Event keys deduplicate retries; changed requests conflict. |
| `claim_job` | Worker | Claims one available job with a token and expiry. | Concurrent workers must not own the same live claim. |
| `complete_job` | Worker | Saves result and successful job state atomically. | An old lease token cannot publish a stale result. |
| `claim_outbox` / `deliver_local` | Local dispatcher | Claims and records one simulated delivery. | Model output cannot choose a new recipient or duplicate local receipts. |

The model does not choose user identity, database connection strings, queue state, or message recipients. Even if later models issue a forbidden tool call, the backend remains responsible for refusing it.

## Why these design choices matter

**Why a modular monolith?** One application is easier to operate locally, but dependency tests keep domain rules independent of adapters. Extraction into services remains possible when scale or ownership requires it. Microservices were not necessary to demonstrate the boundaries.

**Why adapters?** They make provider-specific concerns replaceable. PostgreSQL implements the repository contract; an in-memory adapter remains useful for fast tests. A future model provider should change message translation and capability handling, not the arithmetic or authorization rules.

**Why integer cents?** Binary floating-point can introduce rounding surprises in money. Package counts use explicit integer quantities and ceiling division. The current currency is USD, and units are explicit; multi-currency and unit conversion would require additional rules.

**Why not let the model pick the cheapest basket?** The model can interpret intent, but deterministic code computes totals and enforces constraints. The current algorithm enumerates bounded store subsets and supports fixed store fees. It is not a universal optimizer for coupons, routes, or split quantities.

**What is an invariant?** A condition that must remain true: an offer belongs to this mission, totals match selected packages, one event creates one job, or an expired worker cannot finish. These are directly testable even with no LLM.

**What is idempotency?** Retrying the same intended operation does not create a second effect. The unique user/event pair identifies submission retries, while a request hash prevents a changed payload from being silently treated as the same request. A generated mission ID is not the retry key.

**Why `SKIP LOCKED`?** It lets competing workers skip jobs already being claimed instead of waiting behind them. The selection and claim update happen inside one transaction. Provider/model work happens after that transaction ends.

**What is a lease token?** A temporary proof that a particular worker owns the claim. Reclaiming a job issues a new token. Completion checks the token and expiry, so an old worker cannot overwrite a newer attempt. Leases provide recovery, not a guarantee that expensive work never runs twice.

**Why an outbox?** Updating job success and saving an outgoing message separately risks one succeeding without the other. An outbox stores delivery intent in the same transaction as completion. A separate dispatcher handles delivery later.

**Do we guarantee exactly-once delivery?** The local mailbox uses one database transaction and a unique receipt ID. That guarantee does not extend to an external messaging API. Live delivery needs idempotency and reconciliation and must handle unknown send outcomes.

**Why no claim that prompt injection is solved?** Deterministic tests prove backend restrictions. A small opt-in local model suite tests development examples, but is not an adversarial benchmark or evidence that prompt injection is solved. Future isolated readers and provenance rules must be evaluated for both attack success and legitimate task completion.

**Why not trust `readOnlyHint`?** It describes a tool; it does not enforce permissions. The application owns the allowlist and checks actual access. A third-party annotation cannot grant authorization.

## Evidence to demonstrate

1. Run the default shopping demo and then tighten its budget. Explain the switch from one store to two.
2. Set an impossible budget. Show that all requested items remain in the result with an over-budget status.
3. Run the forged-reference and cross-user tests. Explain why an ID is not permission by itself.
4. Submit a saved request twice using the same event ID. Show one mission and one job.
5. Run separate worker and mailbox commands. Explain why persistent state survives process exit.
6. Run the expired-lease test. Show that the original worker is rejected after reclaim.
7. Show the rollback tests: mission/job insertion and job/outbox completion succeed together or not at all.
8. Explain that the optimizer oracle uses a different algorithm from the implementation; it does not merely restate the same code.

## Roadmap answers

LangGraph will own model/tool execution flow and checkpoints. NeMo Agent Toolkit will wrap that workflow for configuration, profiling, and evaluation. Anthropic, OpenAI, and local models will sit behind a capability-aware model adapter. Linq will sit behind a channel adapter. The Ollama adapter is connected to the local experiment; those other components are not yet connected.

Before calling this production-ready, add authentication, network-provider deadlines, a real channel outbox, migration/runtime role separation, retention and deletion, backups and restore tests, secret management, service monitoring, and measured model evaluations. Explain those gaps directly; distinguishing implementation from aspiration is part of the engineering work.

## Explain the model boundary

The model makes two different proposals. `IntentModel.extract` suggests a shopping draft; `interpret` validates it and converts explicit litres/kilograms into base units. Missing fields produce questions. `ModelAdapter.propose` suggests the single allowed tool call; the adapter maps short call-local labels back to scoped IDs, and the gateway checks those references before any comparison executes. Prices come from stored offers and code, never model-written prose.

The second call currently has no real decision to make because there is one permitted tool and all references are required. It is a capability/integration experiment, with extra latency, not an optimization. A production fixed path can call the comparator directly. Add a richer agent loop only when there are genuine tool-selection decisions. A model can still misunderstand a request or miss an allergy; source checks and JSON schemas do not establish semantic correctness. The experiment displays the draft and cannot purchase or save personal preferences.

## Explain persistent draft updates

A draft is the request under clarification or review; a mission describes executable shopping requirements; a job tells a worker to perform that mission. The new draft repository persists incomplete and reviewed requests separately from jobs. Confirmation alone does not enqueue work.

A revision number identifies the exact snapshot being edited or approved. The PostgreSQL adapter locks the owner's current row, checks the expected revision, validates the transition, and saves the new snapshot in one transaction. Two edits starting from revision 1 cannot both replace it: the second sees a conflict after the first commits revision 2. Approval is checked against the unchanged saved content and database time, preventing an old approval from silently approving a changed budget.

Model inference happens before the transaction, keeping row locks short. The repository validates JSON snapshots without inference. Tests use real competing PostgreSQL connections and separate Python processes. This is application-level ownership enforcement, not PostgreSQL row-level security. Conversation routing, draft event deduplication, history, and atomic job submission are still future work.
