"""Snapshot validation and opt-in real database concurrency tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import unittest

from icart.adapters.draft_serialization import draft_from_data, draft_to_data
from icart.application.drafts import RevisionConflict, confirm_draft, revise_draft, start_draft
from icart.application.security import PolicyViolation
from icart.domain.models import Principal, ValidationError
from test_drafts import StubIntent, TEXT


USER = Principal("alice")
DSN = os.environ.get("ICART_TEST_DATABASE_URL")


def draft(budget=None):
    return start_draft(TEXT, StubIntent(budget), USER, datetime.now(timezone.utc))


class DraftCodecTests(unittest.TestCase):
    def test_roundtrip_preserves_incomplete_and_complete_snapshots(self):
        for value in (draft(), draft(2000)):
            self.assertEqual(draft_from_data(json.loads(json.dumps(draft_to_data(value)))), value)

    def test_forged_derived_mission_is_rejected(self):
        data = draft_to_data(draft(2000))
        data["mission"]["budget_cents"] = 9999
        with self.assertRaises(ValidationError):
            draft_from_data(data)

    def test_incomplete_confirmation_is_rejected(self):
        data = draft_to_data(draft())
        data.update(confirmed=True, revision=2)
        with self.assertRaises(ValidationError):
            draft_from_data(data)

    def test_bad_version_revision_and_extra_fields_are_rejected(self):
        for key, value in (("version", 2), ("version", True), ("revision", True), ("revision", 0), ("extra", "bad")):
            data = draft_to_data(draft())
            data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                draft_from_data(data)


@unittest.skipUnless(DSN, "set ICART_TEST_DATABASE_URL for real PostgreSQL draft tests")
class DraftStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        from icart.adapters.postgres import PostgresStore
        from icart.adapters.postgres_drafts import PostgresDraftRepository
        if conninfo_to_dict(DSN).get("dbname") != "icart_test":
            raise RuntimeError("refusing cleanup outside icart_test")
        cls.database = PostgresStore(DSN)
        cls.database.migrate()
        cls.Repository = PostgresDraftRepository
        cls.repo = cls.Repository(cls.database)

    def setUp(self):
        with self.database.connect() as conn:
            conn.execute("TRUNCATE icart_drafts")

    def test_incomplete_survives_a_separate_process(self):
        value = draft()
        self.repo.create(USER, value)
        code = """import os,sys
from icart.adapters.postgres import PostgresStore
from icart.adapters.postgres_drafts import PostgresDraftRepository
from icart.domain.models import Principal
d = PostgresDraftRepository(PostgresStore(os.environ['ICART_TEST_DATABASE_URL'])).load(Principal('alice'),sys.argv[1])
print(d.state.value)
print(d.questions[0])
"""
        result = subprocess.run([sys.executable, "-c", code, value.id], capture_output=True, text=True, check=True)
        self.assertIn("needs_details", result.stdout)
        self.assertIn("maximum budget", result.stdout)

    def test_revision_and_confirmation_roundtrip(self):
        first = draft()
        self.repo.create(USER, first)
        second = revise_draft(first, TEXT + " Budget $20.", StubIntent(2000), USER, 1)
        self.repo.save(USER, second, 1)
        third = confirm_draft(second, USER, 2, datetime.now(timezone.utc))
        self.repo.save(USER, third, 2)
        self.assertEqual(self.repo.load(USER, first.id), third)

    def test_duplicate_create_does_not_overwrite(self):
        value = draft()
        self.repo.create(USER, value)
        with self.assertRaises(RevisionConflict):
            self.repo.create(USER, value)
        self.assertEqual(self.repo.load(USER, value.id), value)

    def test_owner_scope_on_create_read_and_save(self):
        value = draft(2000)
        self.repo.create(USER, value)
        bob = Principal("bob")
        with self.assertRaises(PolicyViolation):
            self.repo.create(bob, value)
        with self.assertRaises(PolicyViolation):
            self.repo.load(bob, value.id)
        with self.assertRaises(PolicyViolation):
            self.repo.save(bob, replace(value, revision=2), 1)
        self.assertEqual(self.repo.load(USER, value.id), value)

    def test_two_concurrent_edits_have_one_winner(self):
        value = draft()
        self.repo.create(USER, value)
        candidates = [revise_draft(value, TEXT, StubIntent(budget), USER, 1) for budget in (2000, 3000)]
        def save(candidate):
            try:
                self.Repository(self.database).save(USER, candidate, 1)
                return "saved"
            except RevisionConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(save, candidates)), ["saved", "conflict"])
        self.assertEqual(self.repo.load(USER, value.id).revision, 2)

    def test_old_approval_cannot_overwrite_a_saved_edit(self):
        value = draft(2000)
        self.repo.create(USER, value)
        approval = confirm_draft(value, USER, 1, datetime.now(timezone.utc))
        edit = revise_draft(value, TEXT, StubIntent(3000), USER, 1)
        self.repo.save(USER, edit, 1)
        with self.assertRaises(RevisionConflict):
            self.repo.save(USER, approval, 1)
        self.assertEqual(self.repo.load(USER, value.id), edit)

    def test_cannot_combine_changed_budget_with_confirmation(self):
        value = draft(2000)
        self.repo.create(USER, value)
        edit = revise_draft(value, TEXT, StubIntent(3000), USER, 1)
        with self.assertRaises(ValidationError):
            self.repo.save(USER, replace(edit, confirmed=True), 1)
        self.assertEqual(self.repo.load(USER, value.id), value)

    def test_confirmed_draft_cannot_reopen(self):
        value = draft(2000)
        self.repo.create(USER, value)
        approved = confirm_draft(value, USER, 1, datetime.now(timezone.utc))
        self.repo.save(USER, approved, 1)
        with self.assertRaises(ValidationError):
            self.repo.save(USER, replace(approved, confirmed=False, revision=3), 2)

    def test_expired_confirmation_rechecked_with_database_time(self):
        past = datetime.now(timezone.utc) - timedelta(hours=7)
        value = start_draft(TEXT, StubIntent(2000), USER, past)
        self.repo.create(USER, value)
        approved = confirm_draft(value, USER, 1, past)
        with self.assertRaises(ValidationError):
            self.repo.save(USER, approved, 1)

    def test_time_anchor_cannot_change(self):
        value = draft(2000)
        self.repo.create(USER, value)
        other = start_draft(TEXT, StubIntent(2000), USER, value.created_at + timedelta(hours=1))
        other = replace(other, id=value.id, revision=2, mission=replace(other.mission, id=value.id))
        with self.assertRaises(ValidationError):
            self.repo.save(USER, other, 1)

    def test_mismatched_database_metadata_is_rejected(self):
        value = draft()
        self.repo.create(USER, value)
        with self.database.connect() as conn:
            conn.execute("UPDATE icart_drafts SET revision=5 WHERE id=%s", (value.id,))
        with self.assertRaises(ValidationError):
            self.repo.load(USER, value.id)
