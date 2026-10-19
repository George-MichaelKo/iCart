# First vertical slice: architecture and walkthrough

## Why a modular monolith

iCart starts as one deployable application with distinct internal modules. This gives a solo developer one place to run and debug it while protecting the boundaries that would otherwise become tangled as integrations grow. Microservices are a deployment choice to justify with measured needs, not a prerequisite for sound modularity.

The original in-memory slice has no server, database, LLM runtime, or network access. Its explicit CLI exercises the actual shopping and policy code against fictional stores. The optional [PostgreSQL slice](persistence.md) now adds persistent records, leased jobs, retries, and a local outbox. The README's larger architecture remains the roadmap.

## Dependency direction

```text
interfaces (CLI and presentation)
         ↓
application (mission workflow, policy, ports)
         ↓
domain (immutable values and basket comparison)

adapters implement application ports
bootstrap chooses and connects the concrete adapters
```

| Module | Responsibility | Must not own |
| --- | --- | --- |
| `domain` | Money, quantities, offer values, basket ranking | HTTP, database clients, model SDKs, channel formatting |
| `application` | Authorize and coordinate a mission; declare integration contracts | Concrete model/provider/storage implementations |
| `adapters` | Fictional catalogs, scripted/Ollama models, offer storage | User-facing output or business-policy overrides |
| `interfaces` | Validate CLI input and render verified records | Invented product prices or authorization from model output |
| `bootstrap.py` | Construct the object graph | Shopping rules |

The architecture test rejects imports that reverse these boundaries. Python protocols describe the ports without requiring concrete adapters to inherit a framework base class. PostgreSQL can later replace the offer repository without making the domain import a database library.

## Follow a request through the code

1. `interfaces.cli` validates explicit item quantities, USD budget, ZIP code, fulfillment, and relative deadline. It creates a local principal and immutable mission. This principal is a demo identity, not production authentication.
2. `bootstrap` wires two fixture product providers, the scripted model, an in-memory repository, and the shopping service.
3. `ShoppingService` checks mission ownership before accessing providers. It runs up to four independent provider searches in parallel, preserving configured provider order when collecting results.
4. The service validates provider identity and result size, then filters by ZIP code, fulfillment, deadline, freshness, stock, and compatible product/unit. Each eligible offer receives a random server-issued reference bound to the user and mission.
5. `ScriptedModel` receives only those references and requests `compare_baskets`. It performs no natural-language reasoning. Raw descriptions, secrets, and identity arguments are absent from its context.
6. `ToolGateway` accepts only the allowed comparison operation and the complete issued reference set. It rejects fabricated, duplicate, hidden, cross-user, or cross-mission references and rechecks eligibility.
7. The domain comparator evaluates store subsets with compatible offers. It computes package counts using integer ceiling division, charges one fixed fulfillment fee per used store, and selects a plan according to budget and convenience.
8. The renderer resolves references again and verifies quantities, totals, status, and coverage. It emits public-facing values without internal references or raw descriptions. Terminal control characters are removed from display fields.

## Ranking and limits

The comparator supports up to 12 distinct items, 200 eligible offers, and eight stores. A configured provider may return at most 50 offers. Candidate generation examines at most 255 nonempty store subsets.

For each subset it chooses an offer that covers each requested quantity. It first favors offers with known modeled charges, then the cheapest compatible package coverage. A complete, verified, under-budget plan ranks by store count and total cost. If no complete plan fits the budget, it returns the cheapest complete verified plan with `over_budget`. If items cannot be found, it reports them explicitly as `incomplete`.

Unknown charges yield `unverified_total` for a complete basket. Partial provider failure appears as a coverage warning even if the surviving provider covers the requested list. An unsupported ZIP code returns missing items, not invented nationwide coverage.

This is exact only within the documented fixed-fee, one-offer-per-item candidate model. It does not model coupons, tiered shipping, route cost, brand substitutions, multi-leg deadlines, or splitting one item's quantity between stores. Unknown-charge candidates are deliberately not claimed to be globally cheapest. A randomized test compares fully priced small baskets against an independent exhaustive assignment oracle.

## Security claims and limits

Implemented: ownership checks, scoped references, a backend-owned tool allowlist, bounded input/provider sizes, immutable domain values, provenance checks, freshness checks, and validated presentation.

The PostgreSQL adapter now implements event deduplication, transactional claims/completion, and persistent mission/offer records. Not implemented: HTTP authentication, webhook verification, personalized memory, recurrence, egress enforcement for network adapters, an isolated reader LLM, or payments. Those cannot be inferred from a passing local test suite.

The injection fixture proves that a raw description does not enter this scripted model's context or presentation. It does not prove that a real model resists prompt injection. Titles remain untrusted display text; an HTML surface must separately escape them. The current process is not a sandbox against malicious Python adapter code.

Parallelism is bounded, but Python threads do not safely cancel a hung provider call. Before adding a network provider, implement transport deadlines and cancellation/recovery behavior. No network provider is enabled today.

## Decisions for this milestone

- Use standard-library Python 3.12 with no runtime dependencies. Source execution works without a package installation or network.
- Keep infrastructure out of the domain and make the model and repository replaceable through ports.
- Use integer USD cents and explicit base units (`count`, `ml`, `g`). No floating-point money or implicit unit conversion.
- Return a complete over-budget basket instead of dropping requirements to make a misleading success.
- Never silently substitute another product key or incompatible unit.
- Add a thin scripted model boundary now; integrate LangGraph and NeMo after the deterministic contracts are stable.
- Keep production integrations disabled until their security, data quality, and cost requirements are addressed.

## Next slice

Persistent mission/offer state, event IDs, transactional job claims, and a local outbox are now implemented behind repository ports; see [the persistence walkthrough](persistence.md). An experimental local model/runtime now has its own [walkthrough and evaluation instructions](local-model.md). It uses the existing gateway and comparator; the persisted worker remains scripted. Real merchant access and Linq should follow those reliability controls, with external delivery reconciliation added at that boundary.

## Natural-language experiment

`natural_cli` invokes the provider-independent `interpret` use case through `IntentModel.extract`. The Ollama adapter requests structured JSON. The application checks its shape and item excerpts, preserves missing fields as questions, and normalizes supported units with deterministic code. When `--plan` is explicitly requested and the draft is complete, `bootstrap.build_local_service(model)` connects the same adapter to the existing `ModelAdapter.propose` port. Native tool output passes through the original gateway. See [local-model.md](local-model.md) for limits; semantic extraction and restriction detection remain model-dependent.

## Persistent draft boundary

The draft lifecycle now has a `DraftRepository` protocol implemented by a separate PostgreSQL adapter. It stores validated JSON snapshots, scopes reads by owner, and locks the current row before checking and advancing its revision. Confirmation rechecks the saved content and database time. Model calls stay outside transactions. See [the persistent-draft walkthrough](persistent-drafts.md); conversation routing and mission/job handoff remain unconnected.
