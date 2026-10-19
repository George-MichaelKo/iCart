# Step 2: saving drafts in PostgreSQL

The draft lifecycle now has a separate PostgreSQL repository. It saves the latest snapshot and revision number, including incomplete requests and their questions. It does not yet associate drafts with channel conversations, merge short answers, keep a revision history, or create execution jobs.

## Components and responsibilities

| Component | Responsibility |
| --- | --- |
| `application.drafts.DraftRepository` | Declares the create, load, and save contract without depending on PostgreSQL. |
| `adapters.draft_serialization` | Encodes version-1 JSON snapshots and checks their consistency when reading or writing. |
| `adapters.postgres_drafts.PostgresDraftRepository` | Enforces owner-scoped reads and transactional revision updates. |
| `002_drafts.sql` | Adds the drafts table and its owner lookup index. The original migration stays unchanged. |
| `examples/persistent_draft.py` | Demonstrates saving and reloading scripted drafts without model calls. |

The adapter reuses `PostgresStore` for connections and migrations. It implements the draft contract separately from the existing job and offer operations. The application layer does not import Psycopg or SQL.

## How an update works

```mermaid
sequenceDiagram
    participant App as Application
    participant Repo as Draft repository
    participant DB as PostgreSQL
    App->>App: Interpret and validate corrected request
    App->>Repo: Save revision 2; expect revision 1
    Repo->>DB: Begin transaction; lock this owner's draft row
    DB-->>Repo: Current snapshot and revision
    alt Current revision is 1 and transition is valid
        Repo->>DB: Update snapshot and revision together
        Repo->>DB: Commit
    else Revision changed or transition is invalid
        Repo->>DB: Roll back
        Repo-->>App: Conflict or validation error
    end
```

`SELECT … FOR UPDATE` temporarily locks the row while the transaction checks and updates it. If two edits both expect revision 1, one saves revision 2; the other sees the changed revision and raises `RevisionConflict`. The application must reload and obtain a new review rather than automatically overwrite the winner. Model inference happens before this short database transaction, never while holding the lock.

Confirmation is checked against the locked snapshot and the database clock. A caller cannot change the budget and confirm that new budget in a single save. An old approval cannot overwrite a newer edit. Confirmed drafts cannot reopen, and corrections cannot move the original deadline's time anchor.

`confirmed` still means approval for planning, not successful submission. Mission/job insertion remains a future atomic handoff. The existing natural-language CLI is not connected to this repository yet.

## What is persisted

The table contains the draft ID, owner, revision, state, a JSON snapshot, and database storage/update timestamps. The snapshot preserves the original time anchor, source request, interpreted fields, questions, and optional candidate mission. The codec revalidates derived fields using deterministic interpretation checks, without calling an LLM. Unknown snapshot versions and inconsistent metadata fail explicitly.

This validates data structure and consistency, not whether a model understood every user requirement. The PostgreSQL credential remains trusted infrastructure; these application checks are not row-level security against arbitrary SQL clients. Only the latest snapshot is kept. Event deduplication for draft creation and channel identity binding are future work.

## Try it locally

Use the existing PostgreSQL setup in [persistence.md](persistence.md). From the repository root, with the container running:

```sh
export ICART_DATABASE_URL='postgresql://icart:icart_local_only@127.0.0.1:55432/icart'
PYTHONPATH=src .venv/bin/python examples/persistent_draft.py
```

These are the repository's local-development credentials, not production credentials. The example applies pending migrations, creates a fictional eggs request, saves a budget correction, and simulates explicit approval. It prints the same ID at revisions 1, 2, and 3. It creates no job and makes no model call or purchase.

After that process exits, replace `PASTE_DRAFT_ID` with its printed ID:

```sh
PYTHONPATH=src .venv/bin/python examples/persistent_draft.py --show PASTE_DRAFT_ID
```

The second process reads the saved `confirmed` draft and its $20 budget. Each run without `--show` creates another synthetic demo draft; it is not an idempotent channel-ingress endpoint. The example is a teaching script, not a production confirmation UI.

## What the tests prove

The real PostgreSQL cases exercise separate-process reads, revision/confirmation roundtrips, duplicate-ID creation rejection, ownership, competing edits, stale approval, changed-content approval rejection, terminal states, database-time expiry, time-anchor preservation, and corrupted metadata. Codec tests verify actual JSON roundtrips, malformed snapshots, and mismatched derived missions.

The older migration checksum test was narrowed to modify only the migration it restores. With multiple migrations, its previous broad update left another test checksum altered. Applied migration files were not rewritten.

Next: conversation association and short-answer handling, followed by transactional submission of an approved draft. Authentication, retention/deletion, and production database roles remain necessary before real-user deployment.
