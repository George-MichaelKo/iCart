"""A bounded mission use case with dependency-injected providers and model."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from icart.application.ports import ModelAdapter, ModelContext, ProductProvider, ToolCall
from icart.application.security import OfferRegistry, PolicyViolation, eligible, require_unique_refs
from icart.domain.models import Mission, Offer, Plan, Principal, ValidationError
from icart.domain.planning import compare_baskets


@dataclass(frozen=True)
class MissionResult:
    plan: Plan
    warnings: tuple[str, ...]


class ToolGateway:
    """Only comparison is exposed in this milestone: no writes or arbitrary URLs."""

    def __init__(self, registry: OfferRegistry) -> None:
        self.registry = registry

    def execute(self, principal: Principal, mission: Mission, call: ToolCall,
                issued_refs: tuple[str, ...], now: datetime) -> Plan:
        self.registry.authorize(principal, mission)
        if not isinstance(call, ToolCall) or call.name != "compare_baskets":
            raise PolicyViolation("tool is not allowed")
        require_unique_refs(call.offer_refs)
        # The model cannot hide offers to manipulate basket ranking.
        if set(call.offer_refs) != set(issued_refs):
            raise PolicyViolation("comparison must use the issued offer set")
        records = tuple(self.registry.resolve(principal, mission, ref) for ref in call.offer_refs)
        if any(not eligible(mission, r.offer, now) for r in records):
            raise PolicyViolation("offer eligibility changed; refresh the search")
        return compare_baskets(mission, records)


class ShoppingService:
    def __init__(self, providers: tuple[ProductProvider, ...], model: ModelAdapter,
                 registry: OfferRegistry) -> None:
        if not 1 <= len(providers) <= 4:
            raise ValidationError("configure between one and four providers")
        if len({p.provider_id for p in providers}) != len(providers):
            raise ValidationError("provider identifiers must be unique")
        self.providers = providers
        self.model = model
        self.registry = registry

    def run(self, principal: Principal, mission: Mission, now: datetime) -> MissionResult:
        self.registry.authorize(principal, mission)
        if mission.deadline < now:
            raise ValidationError("mission deadline has already passed")
        warnings: list[str] = []
        refs: list[str] = []
        # Bounded fan-out over local fixtures. Real adapters must enforce transport
        # deadlines; cancelling a Python thread cannot stop a hung HTTP request.
        with ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures = [(p, pool.submit(p.search, mission, now)) for p in self.providers]
            for provider, future in futures:
                try:
                    offers = future.result()
                    if not isinstance(offers, tuple) or len(offers) > 50:
                        raise ValidationError("invalid or excessive provider result")
                    if any(not isinstance(o, Offer) or o.provider_id != provider.provider_id for o in offers):
                        raise ValidationError("provider provenance mismatch")
                except (ValueError, OSError, TimeoutError, AttributeError):
                    warnings.append(f"Provider {provider.provider_id} failed; coverage is partial.")
                    continue
                for offer in offers:
                    if eligible(mission, offer, now):
                        refs.append(self.registry.register(principal, mission, offer))
        issued = tuple(refs)
        call = self.model.propose(ModelContext(issued))
        plan = ToolGateway(self.registry).execute(principal, mission, call, issued, now)
        return MissionResult(plan, tuple(warnings))
