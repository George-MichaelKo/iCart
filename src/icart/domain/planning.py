"""Bounded, deterministic basket comparison over normalized offers."""

from itertools import combinations

from icart.domain.models import (
    Mission, Plan, PlanStatus, RegisteredOffer, Selection, ValidationError,
)


def compare_baskets(mission: Mission, offers: tuple[RegisteredOffer, ...]) -> Plan:
    """Compare store subsets, then choose the cheapest package coverage per item.

    Supports fixed per-store fulfillment fees, no coupons or tiered fees, and
    one offer per item. Does not split an item's quantity across stores. Those
    limitations make the cost model explicit rather than imply global optimality.
    Eligibility/freshness is enforced by the gateway before this pure function.
    """
    stores = sorted({(x.offer.provider_id, x.offer.store_id) for x in offers})
    if len(stores) > 8 or len(offers) > 200:
        raise ValidationError("catalog exceeds the bounded comparison limit")
    fees: dict[tuple[str, str], int] = {}
    for record in offers:
        key = (record.offer.provider_id, record.offer.store_id)
        fee = record.offer.fulfillment_fee_cents
        if key in fees and fees[key] != fee:
            raise ValidationError("inconsistent fixed fee for a store")
        fees[key] = fee

    plans: list[Plan] = []
    subsets = [()] if not stores else [
        subset for n in range(1, len(stores) + 1) for subset in combinations(stores, n)
    ]
    for subset in subsets:
        selected: list[Selection] = []
        missing: list[str] = []
        used: set[tuple[str, str]] = set()
        total_known = True
        for item in mission.items:
            candidates = []
            for record in offers:
                offer = record.offer
                if (offer.provider_id, offer.store_id) not in subset:
                    continue
                if (offer.product_key, offer.unit) != (item.product_key, item.unit):
                    continue
                packages = (item.quantity + offer.package_quantity - 1) // offer.package_quantity
                if packages <= offer.stock_packages:
                    candidates.append((not offer.total_known, packages * offer.price_cents,
                                       offer.provider_id, offer.source_id, record, packages))
            if not candidates:
                missing.append(item.product_key)
                continue
            _, cost, _, _, record, packages = min(candidates, key=lambda c: c[:4])
            selected.append(Selection(record.ref, item.product_key, packages, cost))
            used.add((record.offer.provider_id, record.offer.store_id))
            total_known = total_known and record.offer.total_known
        subtotal = sum(x.subtotal_cents for x in selected)
        fee_total = sum(fees[key] for key in used)
        total = subtotal + fee_total
        status = (PlanStatus.INCOMPLETE if missing else
                  PlanStatus.UNVERIFIED_TOTAL if not total_known else
                  PlanStatus.OVER_BUDGET if total > mission.budget_cents else PlanStatus.READY)
        plans.append(Plan(mission.id, status, tuple(selected), tuple(missing), subtotal,
                          fee_total, total, len(used), total_known))

    # Never drop items merely to manufacture an under-budget result.
    complete = [p for p in plans if not p.missing]
    feasible = [p for p in complete if p.status == PlanStatus.READY]
    if feasible:
        return min(feasible, key=lambda p: (p.store_count, p.total_cents))
    if complete:
        return min(complete, key=lambda p: (not p.total_known, p.total_cents, p.store_count))
    return min(plans, key=lambda p: (len(p.missing), not p.total_known, p.store_count, p.total_cents))
