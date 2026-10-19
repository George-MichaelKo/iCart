"""Immutable domain values; money is represented in integer USD cents."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re


class ValidationError(ValueError):
    """A value cannot safely participate in a shopping mission."""


def positive_int(value: int, name: str, *, allow_zero: bool = False) -> None:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValidationError(f"{name} must be an integer {'>= 0' if allow_zero else '> 0'}")


def aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("timestamps must include a time zone")


class Fulfillment(StrEnum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


@dataclass(frozen=True)
class Principal:
    """Supplied by the interface/authentication boundary, never a model tool arg."""

    user_id: str

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValidationError("user identity is required")


@dataclass(frozen=True)
class LineItem:
    product_key: str
    quantity: int
    unit: str

    def __post_init__(self) -> None:
        positive_int(self.quantity, "quantity")
        if self.quantity > 10_000:
            raise ValidationError("quantity exceeds local mission limit")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.product_key):
            raise ValidationError("invalid product key")
        if self.unit not in {"count", "ml", "g"}:
            raise ValidationError("unit must be count, ml, or g")


@dataclass(frozen=True)
class Mission:
    id: str
    owner_id: str
    items: tuple[LineItem, ...]
    budget_cents: int
    postal_code: str
    fulfillment: Fulfillment
    deadline: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.owner_id:
            raise ValidationError("mission identity and owner are required")
        if not 1 <= len(self.items) <= 12:
            raise ValidationError("a mission must contain 1–12 distinct items")
        if len({item.product_key for item in self.items}) != len(self.items):
            raise ValidationError("merge duplicate product keys before creating a mission")
        positive_int(self.budget_cents, "budget", allow_zero=True)
        if not re.fullmatch(r"\d{5}", self.postal_code):
            raise ValidationError("a five-digit US ZIP code is required")
        if not isinstance(self.fulfillment, Fulfillment):
            raise ValidationError("unsupported fulfillment method")
        aware(self.deadline)


@dataclass(frozen=True)
class Offer:
    """Normalized provider data; never itself a permission or a verified user fact.

    Fixture totals include all modeled charges. Real providers must not set
    total_known unless their integration can substantiate that claim.
    """

    provider_id: str
    source_id: str
    store_id: str
    store_name: str
    product_key: str
    title: str
    package_quantity: int
    unit: str
    price_cents: int
    fulfillment_fee_cents: int
    postal_code: str
    fulfillment: Fulfillment
    available_at: datetime
    observed_at: datetime
    expires_at: datetime
    stock_packages: int
    description: str = ""
    total_known: bool = True
    synthetic: bool = True

    def __post_init__(self) -> None:
        for name in ("provider_id", "source_id", "store_id", "store_name", "title"):
            if not getattr(self, name):
                raise ValidationError(f"{name} is required")
        LineItem(self.product_key, self.package_quantity, self.unit)
        positive_int(self.price_cents, "price", allow_zero=True)
        positive_int(self.fulfillment_fee_cents, "fee", allow_zero=True)
        positive_int(self.stock_packages, "stock", allow_zero=True)
        for timestamp in (self.available_at, self.observed_at, self.expires_at):
            aware(timestamp)
        if self.expires_at <= self.observed_at:
            raise ValidationError("offer expiry must follow observation")
        if type(self.total_known) is not bool or type(self.synthetic) is not bool:
            raise ValidationError("offer status flags must be boolean")
        if not isinstance(self.fulfillment, Fulfillment):
            raise ValidationError("unsupported offer fulfillment")


@dataclass(frozen=True)
class RegisteredOffer:
    ref: str
    offer: Offer


@dataclass(frozen=True)
class Selection:
    offer_ref: str
    product_key: str
    packages: int
    subtotal_cents: int


class PlanStatus(StrEnum):
    READY = "ready"
    OVER_BUDGET = "over_budget"
    INCOMPLETE = "incomplete"
    UNVERIFIED_TOTAL = "unverified_total"


@dataclass(frozen=True)
class Plan:
    mission_id: str
    status: PlanStatus
    selections: tuple[Selection, ...]
    missing: tuple[str, ...]
    subtotal_cents: int
    fees_cents: int
    total_cents: int
    store_count: int
    total_known: bool
