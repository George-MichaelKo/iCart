import unicodedata
from datetime import datetime

from icart.application.security import OfferRegistry, PolicyViolation, eligible
from icart.application.service import MissionResult
from icart.domain.models import Mission, PlanStatus, Principal


def display_text(value: str) -> str:
    # Strip terminal escapes/control characters and bidi format controls.
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))[:160]


def money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def render(principal: Principal, mission: Mission, result: MissionResult,
           registry: OfferRegistry, now: datetime) -> dict:
    registry.authorize(principal, mission)
    plan = result.plan
    if plan.mission_id != mission.id:
        raise PolicyViolation("plan does not belong to this mission")
    items = []
    store_fees = {}
    all_synthetic = True
    total_known = True
    for selection in plan.selections:
        offer = registry.resolve(principal, mission, selection.offer_ref).offer
        if not eligible(mission, offer, now):
            raise PolicyViolation("offer expired or is no longer eligible; refresh before rendering")
        line = next((i for i in mission.items if i.product_key == selection.product_key), None)
        if line is None or (offer.product_key, offer.unit) != (line.product_key, line.unit):
            raise PolicyViolation("offer does not match the requested item")
        expected_packages = (line.quantity + offer.package_quantity - 1) // offer.package_quantity
        if (selection.packages != expected_packages or expected_packages > offer.stock_packages or
                selection.subtotal_cents != expected_packages * offer.price_cents):
            raise PolicyViolation("invalid presentation quantity or price")
        store_fees[(offer.provider_id, offer.store_id)] = offer.fulfillment_fee_cents
        all_synthetic = all_synthetic and offer.synthetic
        total_known = total_known and offer.total_known
        items.append({
            "item": selection.product_key, "title": display_text(offer.title),
            "store": display_text(offer.store_name), "packages": selection.packages,
            "package_quantity": offer.package_quantity, "unit": offer.unit,
            "subtotal_cents": selection.subtotal_cents,
            "available_at": offer.available_at.isoformat(),
            "observed_at": offer.observed_at.isoformat(),
        })
    subtotal = sum(item["subtotal_cents"] for item in items)
    fees = sum(store_fees.values())
    selected_keys = [item["item"] for item in items]
    expected_missing = tuple(i.product_key for i in mission.items if i.product_key not in selected_keys)
    if (len(set(selected_keys)) != len(selected_keys) or plan.missing != expected_missing or
            (subtotal, fees, subtotal + fees, len(store_fees)) !=
            (plan.subtotal_cents, plan.fees_cents, plan.total_cents, plan.store_count)):
        raise PolicyViolation("plan totals or coverage are inconsistent")
    expected_status = (PlanStatus.INCOMPLETE if expected_missing else
                       PlanStatus.UNVERIFIED_TOTAL if not total_known else
                       PlanStatus.OVER_BUDGET if subtotal + fees > mission.budget_cents else
                       PlanStatus.READY)
    if plan.total_known != total_known or plan.status != expected_status:
        raise PolicyViolation("plan status is inconsistent with authoritative records")
    summaries = {
        PlanStatus.READY: "Your shopping plan is ready 🛒",
        PlanStatus.OVER_BUDGET: "The complete basket exceeds your budget.",
        PlanStatus.INCOMPLETE: "Some requested items could not be covered.",
        PlanStatus.UNVERIFIED_TOTAL: "Additional charges are unknown; budget compliance is unverified.",
    }
    return {
        "mode": "synthetic" if all_synthetic else "provider_data",
        "summary": summaries[plan.status], "status": plan.status.value,
        "fulfillment": mission.fulfillment.value, "postal_code": mission.postal_code,
        "budget_cents": mission.budget_cents, "items": items,
        "missing_items": list(plan.missing), "subtotal_cents": subtotal,
        "fees_cents": fees, "total_cents": subtotal + fees,
        "total_known": plan.total_known, "store_count": len(store_fees),
        "warnings": list(result.warnings),
        "checkout_available": False,
    }


def as_text(view: dict) -> str:
    lines = ["iCart · LOCAL SYNTHETIC DEMO · no purchases or live prices", view["summary"], ""]
    for index, item in enumerate(view["items"], 1):
        lines.append(f"{index}. {item['title']} × {item['packages']} — "
                     f"{money(item['subtotal_cents'])} at {item['store']}")
    lines.extend(["", f"Items: {money(view['subtotal_cents'])} | Modeled fees: {money(view['fees_cents'])}",
                  f"{'Total' if view['total_known'] else 'Known charges'}: {money(view['total_cents'])} "
                  f"| Budget: {money(view['budget_cents'])} | Stores: {view['store_count']}"])
    if view["missing_items"]:
        lines.append("Missing: " + ", ".join(view["missing_items"]))
    lines.extend(view["warnings"])
    lines.append("Fixture charges only; no real taxes, stock, delivery slots, or checkout links.")
    return "\n".join(lines)
