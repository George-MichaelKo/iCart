"""Lifecycle behavior without model inference or a database."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from icart.application.drafts import DraftState, RevisionConflict, confirm_draft, revise_draft, start_draft
from icart.application.security import PolicyViolation
from icart.domain.models import Principal, ValidationError


NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
USER = Principal("alice")
TEXT = "Get 12 eggs for pickup in 60601 within 6 hours."
DATA = {"items": [{"product_key": "eggs", "quantity": 12, "unit": "count", "source_text": "12 eggs"}],
        "budget_cents": None, "postal_code": "60601", "fulfillment": "pickup",
        "within_hours": 6, "unsupported_constraints": []}


class StubIntent:
    def __init__(self, budget=None, restrictions=()):
        self.data = deepcopy(DATA)
        self.data.update(budget_cents=budget, unsupported_constraints=list(restrictions))

    def extract(self, text):
        return self.data


class DraftTests(unittest.TestCase):
    def start(self, budget=None):
        return start_draft(TEXT, StubIntent(budget), USER, NOW)

    def test_missing_budget_then_review_then_confirm(self):
        first = self.start()
        self.assertEqual(first.state, DraftState.NEEDS_DETAILS)
        second = revise_draft(first, TEXT + " Budget $20.", StubIntent(2000), USER, 1)
        self.assertEqual(second.state, DraftState.NEEDS_REVIEW)
        third = confirm_draft(second, USER, 2, NOW + timedelta(minutes=5))
        self.assertEqual(third.state, DraftState.CONFIRMED)
        self.assertEqual(third.mission.budget_cents, 2000)
        self.assertEqual((first.revision, second.revision, third.revision), (1, 2, 3))
        self.assertEqual(first.state, DraftState.NEEDS_DETAILS)

    def test_complete_input_still_requires_review(self):
        self.assertEqual(self.start(2000).state, DraftState.NEEDS_REVIEW)

    def test_incomplete_cannot_confirm(self):
        with self.assertRaises(ValidationError):
            confirm_draft(self.start(), USER, 1, NOW)

    def test_unsupported_constraint_cannot_confirm(self):
        draft = start_draft(TEXT, StubIntent(2000, ("weekly recurrence",)), USER, NOW)
        with self.assertRaises(ValidationError):
            confirm_draft(draft, USER, 1, NOW)

    def test_old_review_cannot_approve_changed_budget(self):
        revised = revise_draft(self.start(2000), TEXT + " Budget $30.", StubIntent(3000), USER, 1)
        with self.assertRaises(RevisionConflict):
            confirm_draft(revised, USER, 1, NOW)
        self.assertEqual(confirm_draft(revised, USER, 2, NOW).mission.budget_cents, 3000)

    def test_stale_revision_rejected_before_model_call(self):
        class NeverModel:
            def extract(self, text):
                raise AssertionError("must not call model")
        for revision in (0, 2, True):
            with self.subTest(revision=revision), self.assertRaises(RevisionConflict):
                revise_draft(self.start(), TEXT, NeverModel(), USER, revision)

    def test_other_user_cannot_edit_or_confirm(self):
        draft = self.start(2000)
        with self.assertRaises(PolicyViolation):
            revise_draft(draft, TEXT, StubIntent(3000), Principal("bob"), 1)
        with self.assertRaises(PolicyViolation):
            confirm_draft(draft, Principal("bob"), 1, NOW)

    def test_confirmed_draft_is_closed(self):
        draft = confirm_draft(self.start(2000), USER, 1, NOW)
        with self.assertRaises(ValidationError):
            revise_draft(draft, TEXT, StubIntent(3000), USER, 2)
        with self.assertRaises(ValidationError):
            confirm_draft(draft, USER, 2, NOW)

    def test_correction_preserves_identity_and_deadline(self):
        first = self.start(2000)
        second = revise_draft(first, TEXT, StubIntent(3000), USER, 1)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.mission.id, second.mission.id)
        self.assertEqual(first.mission.deadline, second.mission.deadline)

    def test_expired_or_backward_time_cannot_confirm(self):
        for now in (NOW - timedelta(seconds=1), NOW + timedelta(hours=6)):
            with self.subTest(now=now), self.assertRaises(ValidationError):
                confirm_draft(self.start(2000), USER, 1, now)

    def test_model_dictionary_cannot_mutate_saved_snapshot(self):
        model = StubIntent(2000)
        draft = start_draft(TEXT, model, USER, NOW)
        model.data["items"][0]["quantity"] = 99
        self.assertEqual(json.loads(draft.intent_json)["items"][0]["quantity"], 12)
        self.assertEqual(draft.mission.items[0].quantity, 12)
