"""Versioned draft snapshots validated on both sides of the storage boundary."""

from dataclasses import replace
from datetime import datetime
import json
import re

from icart.adapters.serialization import mission_to_data
from icart.application.drafts import ShoppingDraft, start_draft
from icart.domain.models import Principal, ValidationError


def draft_to_data(draft: ShoppingDraft) -> dict:
    return {"version": 1, "id": draft.id, "owner_id": draft.owner_id, "revision": draft.revision,
            "created_at": draft.created_at.isoformat(), "source_text": draft.source_text,
            "intent": json.loads(draft.intent_json), "questions": list(draft.questions),
            "mission": json.loads(json.dumps(mission_to_data(draft.mission))) if draft.mission else None,
            "confirmed": draft.confirmed}


def draft_from_data(data: dict) -> ShoppingDraft:
    expected = {"version", "id", "owner_id", "revision", "created_at", "source_text",
                "intent", "questions", "mission", "confirmed"}
    try:
        if not isinstance(data, dict) or set(data) != expected or type(data["version"]) is not int or data["version"] != 1:
            raise ValueError()
        if not isinstance(data["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", data["id"]):
            raise ValueError()
        if not isinstance(data["owner_id"], str) or not 1 <= len(data["owner_id"]) <= 128:
            raise ValueError()
        if type(data["revision"]) is not int or not 1 <= data["revision"] <= 2_147_483_647:
            raise ValueError()
        if type(data["confirmed"]) is not bool:
            raise ValueError()

        class SavedIntent:
            def extract(self, text):
                return data["intent"]

        # Reuse deterministic validation; this makes no inference/network call.
        rebuilt = start_draft(data["source_text"], SavedIntent(), Principal(data["owner_id"]),
                              datetime.fromisoformat(data["created_at"]))
        mission = replace(rebuilt.mission, id=data["id"]) if rebuilt.mission else None
        rebuilt = replace(rebuilt, id=data["id"], revision=data["revision"], mission=mission,
                          confirmed=data["confirmed"])
        if data["confirmed"] and (mission is None or rebuilt.questions or data["revision"] < 2):
            raise ValueError()
        if draft_to_data(rebuilt) != data:
            raise ValueError()
        return rebuilt
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        raise ValidationError("invalid or unsupported draft snapshot") from None
