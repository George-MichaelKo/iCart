"""Two fictional stores. No real inventory, prices, fees, or checkout access."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from icart.domain.models import Fulfillment, Mission, Offer


@dataclass(frozen=True)
class FixtureProvider:
    provider_id: str
    store_name: str
    catalog: tuple[tuple[str, str, int, str, int], ...]

    def search(self, mission: Mission, now: datetime) -> tuple[Offer, ...]:
        # Chicago fixture coverage is deliberately finite, not nationwide data.
        if mission.postal_code != "60601":
            return ()
        return tuple(
            Offer(
                provider_id=self.provider_id, source_id=f"{self.provider_id}:{key}",
                store_id="60601", store_name=self.store_name, product_key=key,
                title=title, package_quantity=quantity, unit=unit, price_cents=price,
                fulfillment_fee_cents=499 if mission.fulfillment == Fulfillment.DELIVERY else 0,
                postal_code="60601", fulfillment=mission.fulfillment,
                available_at=now + timedelta(hours=2), observed_at=now,
                expires_at=now + timedelta(minutes=30), stock_packages=10,
                description="Synthetic catalog record. Not a real merchant offer.",
            )
            for key, title, quantity, unit, price in self.catalog
        )


def fixture_providers() -> tuple[FixtureProvider, ...]:
    return (
        FixtureProvider("fixture_maple", "Maple Market (fictional)", (
            ("milk", "Milk · 1 litre", 1000, "ml", 350),
            ("eggs", "Eggs · dozen", 12, "count", 400),
            ("rice", "Rice · 1 kilogram", 1000, "g", 500),
            ("chicken", "Chicken · 500 grams", 500, "g", 650),
        )),
        FixtureProvider("fixture_cedar", "Cedar Grocer (fictional)", (
            ("milk", "Milk · 2 litres", 2000, "ml", 600),
            ("eggs", "Eggs · half dozen", 6, "count", 175),
            ("rice", "Rice · 500 grams", 500, "g", 200),
        )),
    )
