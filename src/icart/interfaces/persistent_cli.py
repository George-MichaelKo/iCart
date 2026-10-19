"""Persistent local demo. Infrastructure dependencies are loaded only here."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from icart.application.persistence import IdempotencyConflict, LeaseLost
from icart.application.security import PolicyViolation
from icart.application.worker import run_once
from icart.domain.models import Fulfillment, LineItem, Mission, Principal, ValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent local fixture workflow; no real messages or purchases")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("request", help="print a sample request; save it and reuse for event retries")
    commands.add_parser("migrate", help="apply checked migrations to the configured database")
    submit = commands.add_parser("submit", help="persist a request and one job atomically")
    submit.add_argument("--request", type=Path, required=True)
    submit.add_argument("--event-id", required=True)
    commands.add_parser("work", help="process at most one job")
    commands.add_parser("dispatch", help="deliver at most one outbox record to the local mailbox")
    show = commands.add_parser("show", help="inspect a saved mission for the local demo user")
    show.add_argument("mission_id")
    commands.add_parser("inbox", help="read the persistent local mailbox")
    args = parser.parse_args(argv)
    principal = Principal("local-demo-user")
    if args.command == "request":
        print(json.dumps({"items": [{"product_key": "milk", "quantity": 1000, "unit": "ml"},
                                     {"product_key": "eggs", "quantity": 12, "unit": "count"},
                                     {"product_key": "rice", "quantity": 1000, "unit": "g"}],
                          "budget_cents": 3000, "postal_code": "60601", "fulfillment": "pickup",
                          "deadline": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()}, indent=2))
        return 0
    dsn = os.environ.get("ICART_DATABASE_URL")
    if not dsn:
        parser.error("set ICART_DATABASE_URL to your local PostgreSQL connection")
    try:
        import psycopg
        from icart.adapters.postgres import PostgresStore
        from icart.bootstrap import build_persistent_executor
    except ImportError:
        parser.error("install the optional dependencies from requirements-postgres.txt")
    try:
        store = PostgresStore(dsn)
        if args.command == "migrate":
            store.migrate()
            output = {"status": "migrated"}
        elif args.command == "submit":
            if args.request.stat().st_size > 65536:
                raise ValidationError("request file exceeds 64 KiB")
            data = json.loads(args.request.read_text())
            request = Mission(uuid4().hex, principal.user_id,
                              tuple(LineItem(**i) for i in data["items"]), data["budget_cents"],
                              data["postal_code"], Fulfillment(data["fulfillment"]),
                              datetime.fromisoformat(data["deadline"]))
            if set(data) != {"items", "budget_cents", "postal_code", "fulfillment", "deadline"}:
                raise ValidationError("request contains unsupported fields")
            output = {"mission_id": store.submit(principal, args.event_id, request)}
        elif args.command == "work":
            output = {"status": run_once(store, build_persistent_executor(store))}
        elif args.command == "dispatch":
            lease = store.claim_outbox()
            if lease:
                store.deliver_local(lease)
            output = {"status": "delivered_locally" if lease else "idle"}
        elif args.command == "show":
            output = store.get_mission(principal, args.mission_id)
        else:
            output = store.inbox(principal)
        print(json.dumps(output, indent=2, default=str))
    except (ValidationError, PolicyViolation, IdempotencyConflict, LeaseLost) as exc:
        parser.error(str(exc))
    except (KeyError, TypeError, ValueError, OSError):
        parser.error("invalid request or inaccessible request file")
    except psycopg.Error:
        parser.error("database operation failed; check local configuration and migrations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
