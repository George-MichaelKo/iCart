from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from icart.domain.models import Mission, Offer


class ProductProvider(Protocol):
    provider_id: str

    def search(self, mission: Mission, now: datetime) -> tuple[Offer, ...]: ...


class OfferRepository(Protocol):
    """Persistence contract; authorization stays in the application gateway."""

    def put(self, ref: str, owner_id: str, mission_id: str, offer: Offer) -> None: ...

    def get(self, ref: str) -> tuple[str, str, Offer] | None: ...


@dataclass(frozen=True)
class ToolCall:
    name: str
    offer_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelContext:
    """Deliberately narrow. No credentials, raw descriptions, or user records."""

    available_offer_refs: tuple[str, ...]


class ModelAdapter(Protocol):
    def propose(self, context: ModelContext) -> ToolCall: ...
