"""Explicit structured CLI. Does not pretend to understand natural language."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from uuid import uuid4

from icart.application.security import PolicyViolation
from icart.bootstrap import build_local_service
from icart.domain.models import Fulfillment, LineItem, Mission, Principal, ValidationError
from icart.interfaces.presentation import as_text, render


def budget(value: str) -> int:
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        raise argparse.ArgumentTypeError("budget must be a nonnegative USD amount with at most two decimals")
    try:
        return int(Decimal(value) * 100)
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("invalid budget") from None


def item(value: str) -> LineItem:
    try:
        key, amount, unit = value.split(":")
        return LineItem(key, int(amount), unit)
    except (ValueError, ValidationError):
        raise argparse.ArgumentTypeError("use product:quantity:unit, e.g. milk:1000:ml") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iCart local synthetic shopping mission; no network calls")
    parser.add_argument("--item", type=item, action="append", help="repeat product:quantity:count|ml|g")
    parser.add_argument("--budget", type=budget, default=3000, metavar="USD")
    parser.add_argument("--zip", dest="postal_code", default="60601")
    parser.add_argument("--fulfillment", choices=list(Fulfillment), default="pickup")
    parser.add_argument("--within-hours", type=int, default=6)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.within_hours <= 168:
            raise ValidationError("deadline must be between 1 and 168 hours from now")
        now = datetime.now(timezone.utc)
        principal = Principal("local-demo-user")
        mission = Mission(
            uuid4().hex, principal.user_id,
            tuple(args.item or (LineItem("milk", 1000, "ml"), LineItem("eggs", 12, "count"),
                                LineItem("rice", 1000, "g"))),
            args.budget, args.postal_code, Fulfillment(args.fulfillment),
            now + timedelta(hours=args.within_hours),
        )
        service, registry = build_local_service()
        result = service.run(principal, mission, now)
        view = render(principal, mission, result, registry, datetime.now(timezone.utc))
        print(json.dumps(view, indent=2, ensure_ascii=False) if args.json_output else as_text(view))
    except (ValidationError, PolicyViolation) as exc:
        parser.error(str(exc))
    return 0
