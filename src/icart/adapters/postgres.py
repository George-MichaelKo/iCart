"""PostgreSQL persistence, job leases, and a transactional local outbox.

Each method owns a short transaction. External/model work runs outside those
transactions. All values are bound parameters; SQL identifiers are static.
"""

from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from icart.adapters.serialization import (
    mission_from_data, mission_to_data, offer_from_data, offer_to_data,
)
from icart.application.persistence import IdempotencyConflict, JobLease, LeaseLost, OutboxLease
from icart.application.security import OfferRegistry, PolicyViolation
from icart.domain.models import Mission, Offer, Principal, ValidationError


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def connect(self):
        return psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row,
                               options="-c statement_timeout=10000 -c lock_timeout=5000")

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(731902114)")
            conn.execute("""CREATE TABLE IF NOT EXISTS icart_schema_versions (
                version TEXT PRIMARY KEY, checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
            for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                content = path.read_text()
                checksum = sha256(content.encode()).hexdigest()
                previous = conn.execute("SELECT checksum FROM icart_schema_versions WHERE version=%s",
                                        (path.name,)).fetchone()
                if previous:
                    if previous["checksum"] != checksum:
                        raise ValidationError("applied migration checksum changed; create a new migration")
                    continue
                conn.execute(content, prepare=False)
                conn.execute("INSERT INTO icart_schema_versions(version, checksum) VALUES (%s, %s)",
                             (path.name, checksum))

    def submit(self, principal: Principal, event_id: str, mission: Mission) -> str:
        OfferRegistry.authorize(principal, mission)
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise ValidationError("event ID must contain 1–128 characters")
        data = mission_to_data(mission)
        canonical = {key: value for key, value in data.items() if key != "id"}
        request_hash = sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        with self.connect() as conn:
            created = conn.execute("""INSERT INTO icart_missions
                (id, owner_id, event_id, request_hash, payload) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (owner_id, event_id) DO NOTHING RETURNING id""",
                (mission.id, principal.user_id, event_id, request_hash, Jsonb(data))).fetchone()
            if not created:
                existing = conn.execute("""SELECT id, request_hash FROM icart_missions
                    WHERE owner_id=%s AND event_id=%s""", (principal.user_id, event_id)).fetchone()
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict("event ID already belongs to a different request")
                return existing["id"]
            conn.execute("INSERT INTO icart_jobs(id, mission_id) VALUES (%s,%s)",
                         (uuid4().hex, mission.id))
            return mission.id

    def put(self, ref: str, owner_id: str, mission_id: str, offer: Offer) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO icart_offers(ref,owner_id,mission_id,payload) VALUES (%s,%s,%s,%s)",
                         (ref, owner_id, mission_id, Jsonb(offer_to_data(offer))))

    def get(self, ref: str) -> tuple[str, str, Offer] | None:
        # Internal repository contract. Public access always uses OfferRegistry.
        with self.connect() as conn:
            record = conn.execute("SELECT owner_id, mission_id, payload FROM icart_offers WHERE ref=%s",
                                  (ref,)).fetchone()
        if record is None:
            return None
        return record["owner_id"], record["mission_id"], offer_from_data(record["payload"])

    @staticmethod
    def _lease_seconds(seconds: int) -> None:
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ValidationError("lease must be between 1 and 3600 seconds")

    def claim_job(self, lease_seconds: int = 60) -> JobLease | None:
        self._lease_seconds(lease_seconds)
        token = uuid4().hex
        with self.connect() as conn:
            # Crashed final attempts must not stay 'running' forever.
            conn.execute("""UPDATE icart_jobs SET status='failed', lease_token=NULL,
                lease_until=NULL, error_code='attempts_exhausted'
                WHERE status='running' AND lease_until <= clock_timestamp() AND attempts >= 3""")
            row = conn.execute("""WITH candidate AS (
                SELECT id FROM icart_jobs WHERE attempts < 3 AND (
                    (status='pending' AND available_at <= clock_timestamp()) OR
                    (status='running' AND lease_until <= clock_timestamp()))
                ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE icart_jobs j SET status='running', attempts=j.attempts+1,
                    lease_token=%s, lease_until=clock_timestamp() + %s * interval '1 second'
                FROM candidate c WHERE j.id=c.id RETURNING j.id,j.mission_id,j.attempts""",
                (token, lease_seconds)).fetchone()
            if row is None:
                return None
            mission_row = conn.execute("SELECT payload FROM icart_missions WHERE id=%s",
                                       (row["mission_id"],)).fetchone()
            return JobLease(row["id"], token, mission_from_data(mission_row["payload"]), row["attempts"])

    def _lock_job(self, conn, lease: JobLease) -> None:
        row = conn.execute("""SELECT j.id FROM icart_jobs j JOIN icart_missions m ON m.id=j.mission_id
            WHERE j.id=%s AND j.mission_id=%s AND m.owner_id=%s
                AND j.status='running' AND j.lease_token=%s AND j.lease_until > clock_timestamp()
            FOR UPDATE OF j""", (lease.id, lease.mission.id, lease.mission.owner_id, lease.token)).fetchone()
        if row is None:
            raise LeaseLost("job lease is expired or no longer owned")

    def complete_job(self, lease: JobLease, presentation: dict) -> None:
        with self.connect() as conn:
            self._lock_job(conn, lease)
            conn.execute("INSERT INTO icart_outbox(id,mission_id,payload) VALUES (%s,%s,%s)",
                         (uuid4().hex, lease.mission.id, Jsonb(presentation)))
            conn.execute("""UPDATE icart_jobs SET status='succeeded', lease_token=NULL,
                lease_until=NULL, error_code=NULL WHERE id=%s""", (lease.id,))

    def fail_job(self, lease: JobLease, error_code: str, *, retryable: bool) -> None:
        if error_code not in {"invalid_request", "policy_rejected", "temporary_failure", "worker_failure"}:
            raise ValidationError("unsupported error code")
        with self.connect() as conn:
            self._lock_job(conn, lease)
            conn.execute("""UPDATE icart_jobs SET
                status=CASE WHEN %s AND attempts < 3 THEN 'pending' ELSE 'failed' END,
                available_at=clock_timestamp() + power(2, attempts) * interval '1 second',
                lease_token=NULL, lease_until=NULL, error_code=%s WHERE id=%s""",
                (retryable, error_code, lease.id))

    def get_mission(self, principal: Principal, mission_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("""SELECT m.id,m.payload,j.status,j.attempts,j.error_code,
                o.status AS delivery_status,o.payload AS presentation
                FROM icart_missions m JOIN icart_jobs j ON j.mission_id=m.id
                LEFT JOIN icart_outbox o ON o.mission_id=m.id
                WHERE m.id=%s AND m.owner_id=%s""", (mission_id, principal.user_id)).fetchone()
        if row is None:
            raise PolicyViolation("mission access denied")
        return row

    def claim_outbox(self, lease_seconds: int = 60) -> OutboxLease | None:
        self._lease_seconds(lease_seconds)
        token = uuid4().hex
        with self.connect() as conn:
            row = conn.execute("""WITH candidate AS (
                SELECT id FROM icart_outbox WHERE status='pending' OR
                    (status='sending' AND lease_until <= clock_timestamp())
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE icart_outbox o SET status='sending',lease_token=%s,
                    lease_until=clock_timestamp() + %s * interval '1 second'
                FROM candidate c WHERE o.id=c.id RETURNING o.id""", (token, lease_seconds)).fetchone()
        return OutboxLease(row["id"], token) if row else None

    def deliver_local(self, lease: OutboxLease) -> None:
        with self.connect() as conn:
            row = conn.execute("""SELECT id FROM icart_outbox WHERE id=%s AND status='sending'
                AND lease_token=%s AND lease_until > clock_timestamp() FOR UPDATE""",
                (lease.id, lease.token)).fetchone()
            if row is None:
                raise LeaseLost("outbox lease is expired or no longer owned")
            conn.execute("INSERT INTO icart_mailbox(outbox_id) VALUES (%s) ON CONFLICT DO NOTHING", (lease.id,))
            conn.execute("""UPDATE icart_outbox SET status='delivered',lease_token=NULL,
                lease_until=NULL WHERE id=%s""", (lease.id,))

    def inbox(self, principal: Principal) -> list[dict]:
        with self.connect() as conn:
            return conn.execute("""SELECT o.id,o.mission_id,o.payload,b.delivered_at
                FROM icart_mailbox b JOIN icart_outbox o ON o.id=b.outbox_id
                JOIN icart_missions m ON m.id=o.mission_id WHERE m.owner_id=%s
                ORDER BY b.delivered_at,o.id""", (principal.user_id,)).fetchall()
