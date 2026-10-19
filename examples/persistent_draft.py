"""Scripted storage demonstration; use --show ID to reload in a new process."""

import argparse
from datetime import datetime, timezone
import json
import os

from draft_lifecycle import ExampleIntent
from icart.adapters.draft_serialization import draft_to_data
from icart.adapters.postgres import PostgresStore
from icart.adapters.postgres_drafts import PostgresDraftRepository
from icart.application.drafts import confirm_draft, revise_draft, start_draft
from icart.domain.models import Principal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", metavar="DRAFT_ID")
    args = parser.parse_args()
    dsn = os.environ.get("ICART_DATABASE_URL")
    if not dsn:
        parser.error("set ICART_DATABASE_URL; see docs/persistence.md")
    database = PostgresStore(dsn)
    repo = PostgresDraftRepository(database)
    principal = Principal("local-demo-user")
    if args.show:
        value = repo.load(principal, args.show)
        print(json.dumps({"state": value.state.value, "draft": draft_to_data(value)}, indent=2))
        return
    database.migrate()
    print("SCRIPTED DATABASE DEMO — simulated approval; no model calls or jobs")
    text = "Get 12 eggs for pickup in 60601 within 6 hours."
    value = start_draft(text, ExampleIntent(), principal, datetime.now(timezone.utc))
    repo.create(principal, value)
    print(value.id, value.revision, value.state.value)
    value = revise_draft(repo.load(principal, value.id), text + " Budget $20.", ExampleIntent(2000), principal, 1)
    repo.save(principal, value, 1)
    print(value.id, value.revision, value.state.value)
    value = confirm_draft(repo.load(principal, value.id), principal, 2, datetime.now(timezone.utc))
    repo.save(principal, value, 2)
    print(value.id, value.revision, value.state.value)
    print("Reload this saved draft in another process using --show", value.id)


if __name__ == "__main__":
    main()
