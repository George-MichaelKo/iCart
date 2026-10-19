"""Durable workflow contracts, independent of PostgreSQL and model providers."""

from dataclasses import dataclass
from typing import Protocol

from icart.domain.models import Mission, Principal


class IdempotencyConflict(ValueError):
    """An event key was reused with a different request."""


class LeaseLost(RuntimeError):
    """A worker no longer owns a live claim and must not publish its result."""


@dataclass(frozen=True)
class JobLease:
    id: str
    token: str
    mission: Mission
    attempt: int


@dataclass(frozen=True)
class OutboxLease:
    id: str
    token: str


class WorkflowStore(Protocol):
    def submit(self, principal: Principal, event_id: str, mission: Mission) -> str: ...

    def claim_job(self, lease_seconds: int = 60) -> JobLease | None: ...

    def complete_job(self, lease: JobLease, presentation: dict) -> None: ...

    def fail_job(self, lease: JobLease, error_code: str, *, retryable: bool) -> None: ...

    def get_mission(self, principal: Principal, mission_id: str) -> dict: ...

    def claim_outbox(self, lease_seconds: int = 60) -> OutboxLease | None: ...

    def deliver_local(self, lease: OutboxLease) -> None: ...

    def inbox(self, principal: Principal) -> list[dict]: ...
