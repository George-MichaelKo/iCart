"""Application-owned policy. Model/tool metadata cannot grant permission."""

from datetime import datetime
from uuid import uuid4

from icart.application.ports import OfferRepository
from icart.domain.models import Mission, Offer, Principal, RegisteredOffer, ValidationError


class PolicyViolation(PermissionError):
    pass


class OfferRegistry:
    """Issues scoped references independently of the persistence implementation."""

    def __init__(self, repository: OfferRepository) -> None:
        self.repository = repository

    @staticmethod
    def authorize(principal: Principal, mission: Mission) -> None:
        if principal.user_id != mission.owner_id:
            raise PolicyViolation("mission access denied")

    def register(self, principal: Principal, mission: Mission, offer: Offer) -> str:
        self.authorize(principal, mission)
        ref = f"offer_{uuid4().hex}"
        self.repository.put(ref, principal.user_id, mission.id, offer)
        return ref

    def resolve(self, principal: Principal, mission: Mission, ref: str) -> RegisteredOffer:
        self.authorize(principal, mission)
        record = self.repository.get(ref)
        if record is None or record[:2] != (principal.user_id, mission.id):
            raise PolicyViolation("offer reference is not authorized for this mission")
        return RegisteredOffer(ref, record[2])


def eligible(mission: Mission, offer: Offer, now: datetime) -> bool:
    return (
        offer.postal_code == mission.postal_code
        and offer.fulfillment == mission.fulfillment
        and offer.available_at <= mission.deadline
        and offer.observed_at <= now < offer.expires_at
        and now <= mission.deadline
        and offer.stock_packages > 0
        and any((i.product_key, i.unit) == (offer.product_key, offer.unit) for i in mission.items)
    )


def require_unique_refs(refs: tuple[str, ...]) -> None:
    if not isinstance(refs, tuple) or any(not isinstance(ref, str) for ref in refs):
        raise ValidationError("offer references must be a tuple of strings")
    if len(refs) > 200 or len(set(refs)) != len(refs):
        raise PolicyViolation("duplicate or excessive offer references")
