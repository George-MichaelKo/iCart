"""One bounded unit of worker execution. No model/provider SDK dependencies."""

from datetime import datetime, timezone
from typing import Callable

from icart.application.persistence import JobLease, LeaseLost, WorkflowStore
from icart.application.security import PolicyViolation
from icart.domain.models import ValidationError


def run_once(store: WorkflowStore, execute: Callable[[JobLease, datetime], dict]) -> str:
    lease = store.claim_job()
    if lease is None:
        return "idle"
    try:
        presentation = execute(lease, datetime.now(timezone.utc))
        store.complete_job(lease, presentation)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except Exception as exc:
        # Never persist exception text: SDK/database errors can contain secrets.
        if isinstance(exc, PolicyViolation):
            code, retryable = "policy_rejected", False
        elif isinstance(exc, ValidationError):
            code, retryable = "invalid_request", False
        elif isinstance(exc, (TimeoutError, OSError)):
            code, retryable = "temporary_failure", True
        else:
            code, retryable = "worker_failure", True
        try:
            store.fail_job(lease, code, retryable=retryable)
        except LeaseLost:
            return "lease_lost"
        return "attempt_failed"
