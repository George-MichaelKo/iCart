"""Composition root: the only place concrete adapters are wired together."""

from datetime import datetime, timezone

from icart.adapters.fixtures import fixture_providers
from icart.adapters.mock_model import ScriptedModel
from icart.adapters.memory import InMemoryOfferRepository
from icart.application.security import OfferRegistry
from icart.application.service import ShoppingService
from icart.application.ports import ModelAdapter, OfferRepository
from icart.application.persistence import JobLease
from icart.domain.models import Principal


def build_local_service(model: ModelAdapter | None = None) -> tuple[ShoppingService, OfferRegistry]:
    registry = OfferRegistry(InMemoryOfferRepository())
    return ShoppingService(fixture_providers(), model if model is not None else ScriptedModel(), registry), registry


def build_persistent_executor(repository: OfferRepository):
    """Compose the existing use case with persistent offers and validated output."""
    from icart.interfaces.presentation import render

    def execute(lease: JobLease, now: datetime) -> dict:
        principal = Principal(lease.mission.owner_id)
        registry = OfferRegistry(repository)
        service = ShoppingService(fixture_providers(), ScriptedModel(), registry)
        result = service.run(principal, lease.mission, now)
        return render(principal, lease.mission, result, registry, datetime.now(timezone.utc))

    return execute
