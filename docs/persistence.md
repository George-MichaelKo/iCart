# PostgreSQL missions and recoverable jobs

This slice adds real database persistence while retaining fictional catalogs and a scripted model. The original in-memory CLI still works without dependencies. The persistent CLI is a separate entrypoint, so installing PostgreSQL does not become a requirement for every contributor.

## Run locally

Prerequisites: Python 3.12+, Docker with a running daemon, and free disk space for the database image. Initial package/image downloads require internet access. No paid model, merchant, or messaging services are used.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-postgres.txt
docker compose up -d --wait

# Development-only credentials from compose.yaml. Do not use publicly.
export ICART_DATABASE_URL='postgresql://icart:icart_local_only@127.0.0.1:55432/icart'
export PYTHONPATH=src

.venv/bin/python -m icart.interfaces.persistent_cli migrate
mkdir -p .local
.venv/bin/python -m icart.interfaces.persistent_cli request > .local/request.json
.venv/bin/python -m icart.interfaces.persistent_cli submit \
  --request .local/request.json --event-id demo-001
.venv/bin/python -m icart.interfaces.persistent_cli work
.venv/bin/python -m icart.interfaces.persistent_cli dispatch
.venv/bin/python -m icart.interfaces.persistent_cli inbox
```

`submit` prints the mission ID. Inspect it with `show MISSION_ID`. To demonstrate event deduplication, repeat the exact `submit` command with the saved request file: it returns the existing mission ID. Regenerating the request changes its absolute deadline, so reusing the event ID then correctly produces a conflict. Use a new event ID for a new request.

Every command is a new process. State survives between them. `work` processes at most one job and `dispatch` at most one outgoing record; run them again for additional work. There is no continuously running scheduler, recurring-list feature, or iMessage sender yet.

Stop the local container when finished:

```sh
docker compose stop
```

The named volume retains data. `docker compose up -d --wait` starts it again. Do not remove volumes unless you intend to erase the local database. The database port binds only to `127.0.0.1`. The checked-in credentials and single owner role are for synthetic local development, not production account security.

## Tables and their relationships

```mermaid
erDiagram
    MISSIONS ||--o{ OFFERS : scopes
    MISSIONS ||--|| JOBS : queues
    MISSIONS ||--o| OUTBOX : produces
    OUTBOX ||--o| MAILBOX : delivers_locally
    MISSIONS {
        text id PK
        text owner_id
        text event_id
        text request_hash
        jsonb payload
    }
    JOBS {
        text id PK
        text mission_id FK
        text status
        int attempts
        text lease_token
        timestamptz lease_until
    }
    OFFERS {
        text ref PK
        text owner_id
        text mission_id FK
        jsonb payload
    }
    OUTBOX {
        text id PK
        text mission_id FK
        jsonb payload
        text status
    }
    MAILBOX {
        text outbox_id PK,FK
        timestamptz delivered_at
    }
```

Actual table names use the `icart_` prefix. A migration-history table records each applied SQL file and its checksum. The offer table has a composite foreign key tying mission and owner together. The outbox recipient is derived through the mission owner, not a model-supplied address.

The database stores explicit JSON data, never pickled Python objects. Domain constructors validate deserialized missions and offers. Relational columns hold identity, state, uniqueness, and scheduling data; JSONB stores the typed domain payload. This keeps the first schema manageable while allowing later normalized item tables if query needs justify them.

## Transaction boundaries

**Submission:** insert the mission and its job together. A unique `(owner_id, event_id)` constraint handles concurrent duplicate events. A canonical request hash distinguishes a true retry from a changed request using the same key. The generated mission ID is excluded from the hash.

**Claim:** select one available row using `FOR UPDATE SKIP LOCKED`, then set the running state, increment attempts, and issue a new random lease token in the same transaction. Locked rows are skipped so workers can claim different jobs without waiting on one queue head.

**Execution:** perform provider reads and comparison outside the claim transaction. This avoids holding database locks while a provider or model is slow. Offers are persisted separately and scoped to the immutable mission. An abandoned attempt may leave unused offer records; retention cleanup is future work.

**Completion:** lock the job and verify its current token and lease. Insert the validated presentation into the outbox and mark the job succeeded in one transaction. If either statement fails, both roll back. `PostgresStore.complete_job` is an internal worker API, not a model tool; the composed executor invokes the renderer before passing its output here.

**Local delivery:** claim the outbox with a lease, then insert one mailbox receipt and mark delivered in a single transaction. A unique outbox ID prevents duplicate local mailbox entries.

## Failure and recovery

| Failure | Behavior |
| --- | --- |
| Worker dies before claiming | Job remains pending. |
| Worker dies after claiming | Another worker can reclaim after the lease expires. |
| Original worker returns after reclaim | Its token fails the completion check; no stale outbox write. |
| Temporary execution failure | Retry with exponential delay, up to three total attempts. |
| Invalid mission or policy violation | Terminal failure; do not repeatedly retry forbidden work. |
| Third attempt dies | Next claim sweep marks the expired job failed. |
| Completion transaction fails | Job success and outgoing result both roll back. |
| Dispatcher dies before local delivery | Its outbox lease can be reclaimed. |

Leases default to 60 seconds. Database time determines expiry. Lease duration is bounded to 1–3600 seconds. This slice has no lease heartbeat; tasks must finish inside the lease or be safely retried. Each failed attempt records an allowlisted error code rather than raw exception text that could leak credentials.

The worker is an internal trusted component. Possession of database credentials or arbitrary execution inside an adapter is outside these application-policy protections. Production requires separate migration/runtime roles, network access controls, authentication, secrets management, and backup/restore procedures.

## Real database tests

Create the separate test database once:

```sh
docker compose exec -T postgres createdb -U icart icart_test
export ICART_TEST_DATABASE_URL='postgresql://icart:icart_local_only@127.0.0.1:55432/icart_test'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

If the test database already exists, skip `createdb`. Tests deliberately clear the `icart_*` workflow tables in `icart_test`; they refuse to do so for another database name. Never point that name at a database with data you need to retain.

Tests cover independent-process persistence, migration checksums, concurrent duplicate submissions, concurrent claims, atomic rollback, expired-token rejection, retry backoff, exhausted attempts, cross-user access, and local outbox recovery. Without the test URL, these integration tests are reported as skipped; they are not represented as passing database validation.

## Limits before live messaging

The local mailbox is a delivery simulator. Its atomic database receipt does not prove exactly-once delivery through Linq or another remote service. External sends will need provider idempotency where available, delivery-event reconciliation, bounded retries, and ambiguous-send recovery. They must also revalidate stale recommendations before sending rather than blindly deliver an old shopping snapshot.

This milestone has immutable one-shot missions, not schedule editing or recurring purchases. There are no new model-accessible write tools. It also does not add production authentication, tenant row-level security, high availability, connection pooling, automated retention, or an enterprise SLA.

References: [PostgreSQL locking and SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html), [Psycopg transaction contexts](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).
