"""Ephemeral repository for a single local run; no durability claim."""

from icart.domain.models import Offer


class InMemoryOfferRepository:
    def __init__(self) -> None:
        self._records: dict[str, tuple[str, str, Offer]] = {}

    def put(self, ref: str, owner_id: str, mission_id: str, offer: Offer) -> None:
        if ref in self._records:
            raise ValueError("offer references cannot be overwritten")
        self._records[ref] = (owner_id, mission_id, offer)

    def get(self, ref: str) -> tuple[str, str, Offer] | None:
        return self._records.get(ref)
