"""Pure draft lifecycle; persistence must later enforce revision checks atomically."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import json
from uuid import uuid4
from typing import Protocol

from icart.application.interpretation import IntentModel, interpret
from icart.application.security import PolicyViolation
from icart.domain.models import Mission, Principal, ValidationError, aware


class DraftState(StrEnum):
    NEEDS_DETAILS = "needs_details"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"


class RevisionConflict(ValueError):
    pass


@dataclass(frozen=True)
class ShoppingDraft:
    id: str
    owner_id: str
    revision: int
    created_at: datetime
    source_text: str
    intent_json: str
    questions: tuple[str, ...]
    mission: Mission | None
    confirmed: bool = False

    @property
    def state(self) -> DraftState:
        if self.confirmed:
            return DraftState.CONFIRMED
        return DraftState.NEEDS_DETAILS if self.questions else DraftState.NEEDS_REVIEW


class DraftRepository(Protocol):
    def create(self, principal: Principal, draft: ShoppingDraft) -> None: ...
    def load(self, principal: Principal, draft_id: str) -> ShoppingDraft: ...
    def save(self, principal: Principal, draft: ShoppingDraft, expected_revision: int) -> None: ...


def _interpret(draft_id: str, text: str, model: IntentModel, principal: Principal,
               anchor: datetime, revision: int) -> ShoppingDraft:
    result = interpret(text, model, principal, anchor)
    mission = replace(result.mission, id=draft_id) if result.mission else None
    # Store a value snapshot, not a mutable dictionary shared with the model.
    return ShoppingDraft(draft_id, principal.user_id, revision, anchor, text,
                         json.dumps(result.draft, sort_keys=True, ensure_ascii=True),
                         result.questions, mission)


def start_draft(text: str, model: IntentModel, principal: Principal, now: datetime) -> ShoppingDraft:
    aware(now)
    return _interpret(uuid4().hex, text, model, principal, now, 1)


def _check(draft: ShoppingDraft, principal: Principal, expected_revision: int) -> None:
    if principal.user_id != draft.owner_id:
        raise PolicyViolation("draft access denied")
    if type(expected_revision) is not int or expected_revision != draft.revision:
        raise RevisionConflict("draft changed; review the current revision")
    if draft.confirmed:
        raise ValidationError("confirmed drafts are closed; start a new draft")


def revise_draft(draft: ShoppingDraft, text: str, model: IntentModel,
                 principal: Principal, expected_revision: int) -> ShoppingDraft:
    """Revalidate a complete corrected request; short-answer merging is not implemented.

    Corrections retain the original relative-time anchor. A budget edit must not
    silently extend the deadline. Explicit deadline rescheduling is future work.
    """
    _check(draft, principal, expected_revision)
    return _interpret(draft.id, text, model, principal, draft.created_at, draft.revision + 1)


def confirm_draft(draft: ShoppingDraft, principal: Principal, expected_revision: int,
                  now: datetime) -> ShoppingDraft:
    """Called by a trusted user-confirmation interface, never exposed as an LLM tool.

    Confirmation approves planning only. It creates no job and authorizes no purchase.
    """
    _check(draft, principal, expected_revision)
    aware(now)
    if now < draft.created_at:
        raise ValidationError("confirmation cannot precede draft creation")
    if draft.questions or draft.mission is None:
        raise ValidationError("resolve draft questions before confirmation")
    if draft.mission.deadline <= now:
        raise ValidationError("draft deadline has passed; start a new draft")
    return replace(draft, confirmed=True, revision=draft.revision + 1)
