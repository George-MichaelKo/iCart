"""Explicit JSON codecs. Persist data, never executable Python/pickle objects."""

from dataclasses import asdict
from datetime import datetime

from icart.domain.models import Fulfillment, LineItem, Mission, Offer


def mission_to_data(mission: Mission) -> dict:
    data = asdict(mission)
    data["deadline"] = mission.deadline.isoformat()
    data["fulfillment"] = mission.fulfillment.value
    return data


def mission_from_data(data: dict) -> Mission:
    fields = dict(data)
    fields["items"] = tuple(LineItem(**item) for item in fields["items"])
    fields["deadline"] = datetime.fromisoformat(fields["deadline"])
    fields["fulfillment"] = Fulfillment(fields["fulfillment"])
    return Mission(**fields)


def offer_to_data(offer: Offer) -> dict:
    data = asdict(offer)
    for key in ("available_at", "observed_at", "expires_at"):
        data[key] = getattr(offer, key).isoformat()
    data["fulfillment"] = offer.fulfillment.value
    return data


def offer_from_data(data: dict) -> Offer:
    fields = dict(data)
    for key in ("available_at", "observed_at", "expires_at"):
        fields[key] = datetime.fromisoformat(fields[key])
    fields["fulfillment"] = Fulfillment(fields["fulfillment"])
    return Offer(**fields)
