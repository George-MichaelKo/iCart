"""Real PostgreSQL tests, opt-in and isolated from the demo database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from icart.application.persistence import IdempotencyConflict, LeaseLost
from icart.application.security import OfferRegistry, PolicyViolation
from icart.application.worker import run_once
from icart.bootstrap import build_persistent_executor
from icart.domain.models import Fulfillment, LineItem, Mission, Principal, ValidationError
from icart.adapters.fixtures import fixture_providers


DSN = os.environ.get("ICART_TEST_DATABASE_URL")
USER = Principal("alice")


def request():
    return Mission(uuid4().hex, USER.user_id, (LineItem("milk", 1000, "ml"),), 3000,
                   "60601", Fulfillment.PICKUP, datetime.now(timezone.utc) + timedelta(hours=6))


@unittest.skipUnless(DSN, "set ICART_TEST_DATABASE_URL for real PostgreSQL integration tests")
class PersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        from icart.adapters.postgres import PostgresStore
        if conninfo_to_dict(DSN).get("dbname") != "icart_test":
            raise RuntimeError("refusing integration cleanup outside the dedicated icart_test database")
        cls.Store = PostgresStore
        cls.store = PostgresStore(DSN)
        cls.store.migrate()

    def setUp(self):
        with self.store.connect() as conn:
            conn.execute("TRUNCATE icart_mailbox,icart_outbox,icart_offers,icart_jobs,icart_missions")

    def count(self, table):
        from psycopg import sql
        with self.store.connect() as conn:
            return conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"]

    def expire_job(self, lease):
        with self.store.connect() as conn:
            conn.execute("UPDATE icart_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (lease.id,))

    def test_migration_is_repeatable(self):
        self.store.migrate()
        self.store.migrate()
        self.assertEqual(self.count("icart_schema_versions"), 2)

    def test_duplicate_event_creates_one_mission_and_job(self):
        mission = request()
        first = self.store.submit(USER, "event-a", mission)
        second = self.store.submit(USER, "event-a", replace(mission, id=uuid4().hex))
        self.assertEqual(first, second)
        self.assertEqual(self.count("icart_missions"), 1)
        self.assertEqual(self.count("icart_jobs"), 1)

    def test_reused_event_with_changed_payload_is_rejected(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)
        with self.assertRaises(IdempotencyConflict):
            self.store.submit(USER, "event-a", replace(mission, id=uuid4().hex, budget_cents=1))
        self.assertEqual(self.count("icart_jobs"), 1)

    def test_concurrent_duplicate_submission_is_atomic(self):
        mission = request()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.Store(DSN).submit(USER, "same-event", replace(mission, id=uuid4().hex)), range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.count("icart_missions"), 1)
        self.assertEqual(self.count("icart_jobs"), 1)

    def test_mission_and_job_insert_rollback_together(self):
        # Force job insertion to fail inside the transaction without changing app code.
        with self.store.connect() as conn:
            conn.execute("ALTER TABLE icart_jobs ADD CONSTRAINT test_reject_job CHECK (false) NOT VALID")
        try:
            with self.assertRaises(Exception):
                self.store.submit(USER, "broken-submit", request())
            self.assertEqual(self.count("icart_missions"), 0)
        finally:
            with self.store.connect() as conn:
                conn.execute("ALTER TABLE icart_jobs DROP CONSTRAINT test_reject_job")

    def test_new_repository_instance_reads_saved_mission_and_offer(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)
        offer = fixture_providers()[0].search(mission, datetime.now(timezone.utc))[0]
        ref = OfferRegistry(self.store).register(USER, mission, offer)
        restarted = self.Store(DSN)
        self.assertEqual(restarted.get_mission(USER, mission.id)["payload"]["id"], mission.id)
        self.assertEqual(OfferRegistry(restarted).resolve(USER, mission, ref).offer, offer)

    def test_cross_user_access_and_offer_registration_are_rejected(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)
        with self.assertRaises(PolicyViolation):
            self.store.get_mission(Principal("bob"), mission.id)
        with self.assertRaises(PolicyViolation):
            self.store.submit(Principal("bob"), "bad", mission)
        self.assertEqual(self.store.inbox(Principal("bob")), [])
        import psycopg
        offer = fixture_providers()[0].search(mission, datetime.now(timezone.utc))[0]
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.store.put("forged-owner-ref", "bob", mission.id, offer)

    def test_event_ids_are_scoped_to_owner(self):
        first = request()
        second = replace(request(), owner_id="bob")
        self.store.submit(USER, "same-key", first)
        self.store.submit(Principal("bob"), "same-key", second)
        self.assertEqual(self.count("icart_missions"), 2)

    def test_migration_checksum_change_is_rejected(self):
        with self.store.connect() as conn:
            row = conn.execute("SELECT version,checksum FROM icart_schema_versions").fetchone()
            conn.execute("UPDATE icart_schema_versions SET checksum='tampered' WHERE version=%s", (row["version"],))
        try:
            with self.assertRaises(ValidationError):
                self.store.migrate()
        finally:
            with self.store.connect() as conn:
                conn.execute("UPDATE icart_schema_versions SET checksum=%s WHERE version=%s",
                             (row["checksum"], row["version"]))

    def test_invalid_lease_duration_is_rejected(self):
        for seconds in (0, -1, True, 3601):
            with self.subTest(seconds=seconds), self.assertRaises(ValidationError):
                self.store.claim_job(seconds)

    def test_concurrent_dispatchers_claim_one_message_once(self):
        self.store.submit(USER, "event-a", request())
        run_once(self.store, build_persistent_executor(self.store))
        with ThreadPoolExecutor(max_workers=4) as pool:
            leases = list(pool.map(lambda _: self.Store(DSN).claim_outbox(), range(4)))
        active = [lease for lease in leases if lease is not None]
        self.assertEqual(len(active), 1)
        self.store.deliver_local(active[0])
        self.assertEqual(len(self.store.inbox(USER)), 1)

    def test_concurrent_workers_claim_different_jobs(self):
        for i in range(4):
            self.store.submit(USER, f"event-{i}", request())
        with ThreadPoolExecutor(max_workers=4) as pool:
            leases = list(pool.map(lambda _: self.Store(DSN).claim_job(), range(4)))
        self.assertEqual(len({lease.id for lease in leases}), 4)
        self.assertIsNone(self.store.claim_job())

    def test_expired_worker_cannot_complete_after_reclaim(self):
        self.store.submit(USER, "event-a", request())
        old = self.store.claim_job()
        self.expire_job(old)
        new = self.Store(DSN).claim_job()
        self.assertEqual(old.id, new.id)
        self.assertNotEqual(old.token, new.token)
        self.assertEqual(new.attempt, 2)
        with self.assertRaises(LeaseLost):
            self.store.complete_job(old, {"stale": True})
        with self.assertRaises(LeaseLost):
            self.store.fail_job(old, "worker_failure", retryable=True)
        self.assertEqual(self.count("icart_outbox"), 0)

    def test_crashed_final_attempt_becomes_failed(self):
        self.store.submit(USER, "event-a", request())
        for attempt in range(1, 4):
            lease = self.store.claim_job()
            self.assertEqual(lease.attempt, attempt)
            self.expire_job(lease)
        self.assertIsNone(self.store.claim_job())
        state = self.store.get_mission(USER, lease.mission.id)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_code"], "attempts_exhausted")

    def test_failed_attempt_backs_off_then_recovers(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)

        def fail(lease, now):
            raise TimeoutError("do not persist secret-token")

        self.assertEqual(run_once(self.store, fail), "attempt_failed")
        self.assertIsNone(self.store.claim_job())
        self.assertNotIn("secret-token", str(self.store.get_mission(USER, mission.id)))
        with self.store.connect() as conn:
            conn.execute("UPDATE icart_jobs SET available_at=clock_timestamp()-interval '1 second'")
        self.assertEqual(run_once(self.store, build_persistent_executor(self.store)), "succeeded")
        self.assertEqual(self.store.get_mission(USER, mission.id)["attempts"], 2)

    def test_invalid_request_failure_is_terminal(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)

        def reject(lease, now):
            raise ValidationError("invalid")

        self.assertEqual(run_once(self.store, reject), "attempt_failed")
        self.assertEqual(self.store.get_mission(USER, mission.id)["status"], "failed")

    def test_completion_and_outbox_are_atomic(self):
        mission = request()
        self.store.submit(USER, "event-a", mission)
        lease = self.store.claim_job()
        with self.store.connect() as conn:
            conn.execute("ALTER TABLE icart_outbox ADD CONSTRAINT test_reject_outbox CHECK (false) NOT VALID")
        try:
            with self.assertRaises(Exception):
                self.store.complete_job(lease, {"synthetic": True})
            self.assertEqual(self.store.get_mission(USER, mission.id)["status"], "running")
            self.assertEqual(self.count("icart_outbox"), 0)
        finally:
            with self.store.connect() as conn:
                conn.execute("ALTER TABLE icart_outbox DROP CONSTRAINT test_reject_outbox")

    def test_complete_twice_cannot_duplicate_outbox(self):
        self.store.submit(USER, "event-a", request())
        lease = self.store.claim_job()
        self.store.complete_job(lease, {"synthetic": True})
        with self.assertRaises(LeaseLost):
            self.store.complete_job(lease, {"synthetic": True})
        self.assertEqual(self.count("icart_outbox"), 1)

    def test_local_delivery_is_single_and_scoped(self):
        self.store.submit(USER, "event-a", request())
        self.assertEqual(run_once(self.store, build_persistent_executor(self.store)), "succeeded")
        delivery = self.store.claim_outbox()
        self.store.deliver_local(delivery)
        with self.assertRaises(LeaseLost):
            self.store.deliver_local(delivery)
        self.assertIsNone(self.store.claim_outbox())
        self.assertEqual(len(self.Store(DSN).inbox(USER)), 1)
        self.assertEqual(self.store.inbox(Principal("bob")), [])

    def test_expired_outbox_lease_can_be_recovered(self):
        self.store.submit(USER, "event-a", request())
        run_once(self.store, build_persistent_executor(self.store))
        old = self.store.claim_outbox()
        with self.store.connect() as conn:
            conn.execute("UPDATE icart_outbox SET lease_until=clock_timestamp()-interval '1 second'")
        new = self.store.claim_outbox()
        with self.assertRaises(LeaseLost):
            self.store.deliver_local(old)
        self.store.deliver_local(new)
        self.assertEqual(len(self.store.inbox(USER)), 1)

    def test_cli_survives_separate_processes(self):
        env = dict(os.environ, ICART_DATABASE_URL=DSN)

        def call(*args):
            result = subprocess.run([sys.executable, "-m", "icart.interfaces.persistent_cli", *args],
                                    text=True, capture_output=True, env=env, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(call("request")))
            submitted = call("submit", "--request", str(path), "--event-id", "cli-event")
            duplicate = call("submit", "--request", str(path), "--event-id", "cli-event")
        self.assertEqual(submitted, duplicate)
        self.assertEqual(call("work")["status"], "succeeded")
        self.assertEqual(call("show", submitted["mission_id"])["status"], "succeeded")
        self.assertEqual(call("dispatch")["status"], "delivered_locally")
        self.assertEqual(len(call("inbox")), 1)
