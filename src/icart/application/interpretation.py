"""Provider-independent validation of a model's proposed shopping intent.

A valid JSON shape is not proof of semantic correctness. Keep the user's source
text and expose the interpreted draft; never turn missing values into defaults.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Protocol
from uuid import uuid4

from icart.domain.models import Fulfillment, LineItem, Mission, Principal, ValidationError


class ModelFailure(RuntimeError):
    pass


class IntentModel(Protocol):
    def extract(self, text: str) -> dict: ...


@dataclass(frozen=True)
class Interpretation:
    draft: dict
    questions: tuple[str, ...]
    mission: Mission | None


def interpret(text: str, model: IntentModel, principal: Principal, now: datetime) -> Interpretation:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValidationError("request must contain 1–4000 characters")
    data = model.extract(text)
    expected = {"items", "budget_cents", "postal_code", "fulfillment", "within_hours", "unsupported_constraints"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ModelFailure("model returned an invalid intent shape")
    if not isinstance(data["items"], list) or not 1 <= len(data["items"]) <= 12:
        raise ModelFailure("model must return 1–12 requested items")
    questions = []
    lines = []
    keys = []
    for item in data["items"]:
        if not isinstance(item, dict) or set(item) != {"product_key", "quantity", "unit", "source_text"}:
            raise ModelFailure("model returned an invalid item shape")
        key = item["product_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
            raise ModelFailure("model returned an invalid product key")
        source = item["source_text"]
        if not isinstance(source, str) or not source.strip() or source not in text:
            raise ModelFailure("item evidence must be an exact excerpt from the request")
        keys.append(key)
        quantity, unit = item["quantity"], item["unit"]
        if quantity is not None and (type(quantity) is not int or not 1 <= quantity <= 10_000):
            raise ModelFailure("model returned an invalid quantity")
        if unit is not None and (not isinstance(unit, str) or unit not in {"count", "ml", "l", "g", "kg"}):
            raise ModelFailure("model returned an invalid unit")
        if quantity is None or unit is None:
            questions.append(f"What quantity and unit do you need for {key}?")
        else:
            # The model extracts the stated unit; application code owns conversion.
            aliases = {"ml": r"(?:ml|millilitres?|milliliters?)", "l": r"(?:l|litres?|liters?)",
                       "g": r"(?:g|grams?)", "kg": r"(?:kg|kilograms?)",
                       "count": r"(?:" + re.escape(key.replace("_", " ")) + r"|count|items?|pieces?)"}
            evidence = re.search(r"(?<![\w.])" + str(quantity) + r"\s*(?:of\s+)?" + aliases[unit] + r"\b", source, re.I)
            dozen = unit == "count" and quantity == 12 and re.search(r"\b(?:a|one|1) dozen\b", source, re.I)
            if not evidence and not dozen:
                questions.append(f"Please restate the quantity for {key} using a number and an explicit unit.")
            else:
                normalized_unit = {"l": "ml", "kg": "g"}.get(unit, unit)
                normalized_quantity = quantity * (1000 if unit in {"l", "kg"} else 1)
                if normalized_quantity > 10_000:
                    raise ModelFailure("normalized quantity exceeds the local limit")
                lines.append(LineItem(key, normalized_quantity, normalized_unit))
    if len(set(keys)) != len(keys):
        raise ModelFailure("model returned duplicate product keys; clarify the request")
    budget = data["budget_cents"]
    if budget is None:
        questions.append("What is your maximum budget in USD?")
    elif type(budget) is not int or not 0 <= budget <= 1_000_000:
        raise ModelFailure("model returned an invalid budget")
    postal = data["postal_code"]
    if postal is None:
        questions.append("What is your five-digit US ZIP code?")
    elif not isinstance(postal, str) or not re.fullmatch(r"\d{5}", postal):
        raise ModelFailure("model returned an invalid ZIP code")
    fulfillment = data["fulfillment"]
    if fulfillment is None:
        questions.append("Do you want pickup or delivery?")
    elif not isinstance(fulfillment, str) or fulfillment not in {"pickup", "delivery"}:
        raise ModelFailure("model returned an invalid fulfillment method")
    hours = data["within_hours"]
    if hours is None:
        questions.append("Within how many hours do you need the items? Use 1–168 hours.")
    elif type(hours) is not int or not 1 <= hours <= 168:
        raise ModelFailure("model returned an invalid time window")
    unsupported = data["unsupported_constraints"]
    if not isinstance(unsupported, list) or len(unsupported) > 12 or any(
            not isinstance(value, str) or not value or len(value) > 300 for value in unsupported):
        raise ModelFailure("model returned invalid unsupported constraints")
    if unsupported:
        questions.append("This local prototype cannot enforce: " + "; ".join(unsupported) +
                         ". Those requirements will not be silently ignored.")
    mission = None
    if not questions:
        mission = Mission(uuid4().hex, principal.user_id, tuple(lines), budget, postal,
                          Fulfillment(fulfillment), now + timedelta(hours=hours))
    return Interpretation(data, tuple(questions), mission)
