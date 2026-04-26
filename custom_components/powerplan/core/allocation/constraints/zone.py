"""One space, several ways to heat it: the source selection (D6 §5.7, INV-42).

    cost(source) = price(carrier, now) / efficiency(source, T_out)      €/kWh of HEAT
                 + capacity_penalty   if the carrier is electricity and stage ≥ 2

**COP inverts priority.** A heat pump at COP 3 delivers three kilowatt-hours of
heat per kilowatt-hour drawn where a resistive slab delivers one, so the pump is
kept and the slab is substituted out; shedding the pump to protect the slab trades
3 kW of heat for 1 kW and is strictly backwards. The same arithmetic crosses
carriers: a condensing boiler at η 0.95 on 0.12 €/kWh gas costs 0.126 €/kWh of
heat, a pump at COP 2.8 on 0.35 €/kWh electricity costs 0.125 - a tie, which is the
whole point. At realistic gas prices the two sources sit within a few per cent of
each other, and a boiler that flaps between them every quarter hour is a boiler
that fails. Hence three gates on every switch: a **15 % margin**, a
**confirmation** that the margin held, and a **dwell** on the source that is
running.

**The guards are the invariant.** Substitution never engages a heat pump below
`min_cop` (2.0) - a pump barely better than a resistor, in the weather where it
defrosts, is not something to hand a house to. A zone with one source never
substitutes at all. And a bathroom keeps its own source, whatever the ranking says:
`never_substitute` is the list of loads a zone may never take away.

**A zone selects; it does not cap** (D6 §11). The selection happens in `prepare()`,
before any load is judged - which is what "a demand transform before allocation"
means in the walk - and the effect is delivered the only way a constraint may
deliver anything: the sources that were not chosen are capped to zero and shed with
reason `zone_substituted` (`design/DECISIONS.md` D-0241).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...loads.stores.cop import CopCurve
from ...model import Carrier
from ...pricing.model import Field, FieldKind, Schema
from ..report import ShedReason, ZoneReport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...model import PriceCurve
    from ...strategies import LoadView
    from .base import AllocCtx, Scope, Violation

__all__ = [
    "DEFAULT_CAPACITY_PENALTY",
    "DEFAULT_MIN_COP",
    "DEFAULT_MIN_DWELL_MIN",
    "DEFAULT_SWITCH_CONFIRM_S",
    "DEFAULT_SWITCH_HYSTERESIS",
    "PENALTY_FROM_STAGE",
    "Zone",
    "ZoneChoice",
    "ZoneSource",
]

#: Never engage a heat pump below this COP (D6 §5.7, INV-42).
DEFAULT_MIN_COP = 2.0

#: How much cheaper the alternative must be before a switch is worth it.
DEFAULT_SWITCH_HYSTERESIS = 0.15

#: How long the source that is running keeps the zone, in minutes.
DEFAULT_MIN_DWELL_MIN = 30.0

#: How long the margin must hold before the switch is taken, in seconds: two
#: 15-minute planning cycles (D6 §5.7), counted in wall-clock so the tick rate
#: cannot change the behaviour (`design/DECISIONS.md` D-0243).
DEFAULT_SWITCH_CONFIRM_S = 1800.0

#: What a kWh of electric heat costs extra while the ceiling is at risk, in major
#: units per kWh - v1's fixed stand-in for D2's `marginal_cost` (D6 §5.7, §10).
DEFAULT_CAPACITY_PENALTY = Decimal("1.00")

#: The stage at which "substitution in" becomes a capacity decision (D6 §5.4).
PENALTY_FROM_STAGE = 2

#: Below two sources there is nothing to substitute to, and the zone stands aside
#: however the prices move (INV-42).
_MIN_SOURCES = 2

#: One kilowatt-hour in, one kilowatt-hour of heat out: the default a resistive
#: source carries (D4 §5.14).
_RESISTIVE: Final = CopCurve.flat(1.0)


@dataclass(frozen=True, slots=True)
class ZoneSource:
    """One way of heating a zone: a load, its carrier and its efficiency (D6 §5.7).

    `heat_w` is the heat the source can deliver. It defaults to the load's
    nameplate times its efficiency, which is right for an electric source, and is
    given explicitly for a non-electric one - a 14 kW boiler's nameplate in D6 is
    the 90 W of pump and fan that the *electricity* budget sees, not its gas input
    (`design/DECISIONS.md` D-0242).
    """

    load_id: str
    carrier: Carrier = Carrier.ELECTRICITY
    efficiency: CopCurve = _RESISTIVE
    heat_w: float | None = None

    @property
    def is_heat_pump(self) -> bool:
        """Whether `min_cop` applies: a source that delivers more heat than it takes.

        Physics rather than a flag - η ≤ 1 is a resistor or a boiler, and the COP
        floor is about heat pumps (INV-42).
        """
        return max(cop for _t, cop in self.efficiency.points) > 1.0

    def cop_at(self, outdoor_c: float | None) -> float:
        """Return the efficiency now, pessimistically when the weather is unknown.

        The lowest anchor of the curve when there is no outdoor temperature: a
        missing sensor must not be what engages a heat pump at −18 °C (D4 §5.14,
        INV-15's spirit).
        """
        if outdoor_c is not None:
            return self.efficiency.at(outdoor_c)
        return min(cop for _t, cop in self.efficiency.points)


@dataclass(frozen=True, slots=True)
class ZoneChoice:
    """Which source a zone is on, since when, and what is trying to replace it.

    `since` is the dwell's clock and `candidate_since` the confirmation's; both are
    instants rather than tick counts, so the same behaviour comes out at any tick
    rate (D6 §7, `design/DECISIONS.md` D-0243).
    """

    source: str
    since: datetime
    candidate: str | None = None
    candidate_since: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-able form `AllocState` persists (D6 §7)."""
        return {
            "source": self.source,
            "since": self.since.isoformat(),
            "candidate": self.candidate,
            "candidate_since": (
                None if self.candidate_since is None else self.candidate_since.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ZoneChoice:
        """Return the choice `as_dict()` wrote."""
        candidate_since = data.get("candidate_since")
        return cls(
            source=str(data["source"]),
            since=datetime.fromisoformat(str(data["since"])),
            candidate=data.get("candidate"),
            candidate_since=(
                None if candidate_since is None else datetime.fromisoformat(str(candidate_since))
            ),
        )


@dataclass(frozen=True, slots=True)
class _Ranked:
    """One available source, costed for this tick."""

    source: ZoneSource
    cost: Decimal
    heat_w: float

    @property
    def load_id(self) -> str:
        """The source's load id."""
        return self.source.load_id


class Zone:
    """One zone: its members' demand, its sources, and which of them carries it."""

    key: str
    scope: ClassVar[Scope] = "zone"
    shed_reason: ClassVar[ShedReason] = ShedReason.ZONE_SUBSTITUTED
    schema: ClassVar[Schema] = (
        Field("name", FieldKind.TEXT, required=True),
        Field("members", FieldKind.LIST, required=True),
        Field("sources", FieldKind.LIST, required=True),
        Field("never_substitute", FieldKind.LIST, default=()),
        Field("min_cop", FieldKind.NUMBER, default=DEFAULT_MIN_COP),
        Field(
            "switch_hysteresis",
            FieldKind.NUMBER,
            default=DEFAULT_SWITCH_HYSTERESIS,
            unit="fraction",
            advanced=True,
        ),
        Field(
            "min_dwell_min",
            FieldKind.NUMBER,
            default=DEFAULT_MIN_DWELL_MIN,
            unit="min",
            advanced=True,
        ),
        Field(
            "switch_confirm_s",
            FieldKind.NUMBER,
            default=DEFAULT_SWITCH_CONFIRM_S,
            unit="s",
            advanced=True,
        ),
        Field(
            "capacity_penalty",
            FieldKind.MONEY,
            default=DEFAULT_CAPACITY_PENALTY,
            unit="/kWh",
            advanced=True,
        ),
    )

    def __init__(
        self,
        *,
        key: str,
        members: frozenset[str],
        sources: Sequence[ZoneSource],
        prices: Mapping[Carrier, PriceCurve],
        outdoor_c: float | None = None,
        never_substitute: frozenset[str] = frozenset(),
        min_cop: float = DEFAULT_MIN_COP,
        switch_hysteresis: float = DEFAULT_SWITCH_HYSTERESIS,
        min_dwell_min: float = DEFAULT_MIN_DWELL_MIN,
        switch_confirm_s: float = DEFAULT_SWITCH_CONFIRM_S,
        capacity_penalty: Decimal = DEFAULT_CAPACITY_PENALTY,
        choice: ZoneChoice | None = None,
    ) -> None:
        """Build the zone for this tick; `prices` and `outdoor_c` are this tick's."""
        self.key = key
        self.members = members
        self.sources = tuple(sources)
        self.prices = prices
        self.outdoor_c = outdoor_c
        self.never_substitute = never_substitute
        self.min_cop = min_cop
        self.switch_hysteresis = switch_hysteresis
        self.min_dwell_min = min_dwell_min
        self.switch_confirm_s = switch_confirm_s
        self.capacity_penalty = capacity_penalty
        self._choice = choice
        self._ids = frozenset(source.load_id for source in self.sources)
        self._active = False
        self._reason = "no demand"
        self._chosen: tuple[str, ...] = ()
        self._substituted: tuple[str, ...] = ()
        self._costs: dict[str, float] = {}
        self._excluded: dict[str, str] = {}
        self._demand_w = 0.0

    # ------------------------------------------------------------- the protocol #

    def seed(self, choice: ZoneChoice | None) -> None:
        """Take last tick's source, dwell and candidate from `AllocState` (D6 §7).

        A zone built with an explicit `choice` keeps it; otherwise the allocator
        hands it what it remembered before `prepare`.
        """
        if self._choice is None and choice is not None:
            self._choice = choice

    def prepare(self, ctx: AllocCtx) -> None:
        """Cost every source and pick the one(s) that carry the zone (D6 §5.7)."""
        ranked = self._rank(ctx)
        self._demand_w, wants = self._demand(ctx)
        if not wants:
            self._settle(active=False, reason="no demand")
            return
        if len(self.sources) < _MIN_SOURCES:
            self._settle(active=False, reason="single source")
            return
        if not ranked:
            # Nothing can be costed: the zone has no opinion, and every source keeps
            # whatever the rest of the walk gives it (D6 §8).
            self._settle(active=False, reason="no priced source")
            return
        chosen, reason, choice = self._choose(ranked, ctx)
        self._choice = choice
        self._chosen = chosen
        self._settle(active=True, reason=reason, ctx=ctx)

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return 0 W for a source this zone substituted away, `None` for anyone else."""
        if not self._active or load.load_id not in self._ids:
            return None
        if load.load_id in self.never_substitute or load.load_id in self._chosen:
            return None
        return 0.0

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: a zone is a preference, and preferences never breach (INV-1)."""
        return ()

    def reserve_w(self) -> float:
        """Return 0: a zone pins no power of its own."""
        return 0.0

    # --------------------------------------------------------------- the report #

    def choice(self) -> ZoneChoice | None:
        """Return the choice the next tick starts from: the dwell and the candidate."""
        return self._choice

    def report(self) -> ZoneReport:
        """Return what this zone decided, for `AllocReport.zones` (D6 §4)."""
        return ZoneReport(
            chosen=self._chosen,
            reason=self._reason,
            substituted=self._substituted,
            cost_per_kwh_heat=dict(self._costs),
            excluded=dict(self._excluded),
            demand_w=self._demand_w,
        )

    # -------------------------------------------------------------- internals #

    def _settle(self, *, active: bool, reason: str, ctx: AllocCtx | None = None) -> None:
        """Record the tick's outcome and which sources it holds back."""
        self._active = active
        self._reason = reason
        if not active:
            self._chosen = ()
            self._substituted = ()
            return
        present = () if ctx is None else tuple(load.load_id for load in ctx.loads)
        self._substituted = tuple(
            sorted(
                load_id
                for load_id in self._ids
                if load_id in present
                and load_id not in self._chosen
                and load_id not in self.never_substitute
            )
        )

    def _rank(self, ctx: AllocCtx) -> tuple[_Ranked, ...]:
        """Return the available sources, cheapest €/kWh of heat first (D6 §5.7)."""
        self._costs = {}
        self._excluded = {}
        rows: list[_Ranked] = []
        penalty = self.capacity_penalty if ctx.stage >= PENALTY_FROM_STAGE else Decimal(0)
        for source in self.sources:
            load = ctx.load(source.load_id)
            if load is None:
                self._excluded[source.load_id] = "absent"
                continue
            curve = self.prices.get(source.carrier)
            slot = None if curve is None else curve.price_at(ctx.now)
            if slot is None:
                self._excluded[source.load_id] = "no_price"
                continue
            cop = source.cop_at(self.outdoor_c)
            if cop <= 0.0:
                self._excluded[source.load_id] = "no_efficiency"
                continue
            # Nothing clamps a negative price (INV-51): being paid to consume makes
            # the *inefficient* source the cheaper one per kWh of heat, which is the
            # right answer and falls out of the division.
            cost = slot.total / Decimal(str(cop))
            if source.carrier is Carrier.ELECTRICITY:
                cost += penalty
            self._costs[source.load_id] = float(cost)
            if source.is_heat_pump and cop < self.min_cop:
                self._excluded[source.load_id] = "cop_below_floor"
                continue
            rows.append(
                _Ranked(
                    source=source,
                    cost=cost,
                    heat_w=source.heat_w if source.heat_w is not None else load.nameplate_w * cop,
                )
            )
        incumbent = None if self._choice is None else self._choice.source
        return tuple(
            sorted(rows, key=lambda row: (row.cost, row.load_id != incumbent, row.load_id))
        )

    def _demand(self, ctx: AllocCtx) -> tuple[float, bool]:
        """Return the members' heat demand in watts, and whether anything wants heat.

        A hydronic loop has `nameplate_w = 0` (D4 §5.15): it asks for heat and draws
        none, so the watts can be zero while the zone still has a demand to place.
        The number decides whether one source needs a top-up beside it; the flag
        decides whether the zone acts at all.
        """
        watts = 0.0
        wants = False
        for load in ctx.loads:
            if load.load_id not in self.members or not load.demand.wants:
                continue
            wants = True
            watts += max(0.0, load.demand.max_w)
        return watts, wants

    def _choose(
        self, ranked: tuple[_Ranked, ...], ctx: AllocCtx
    ) -> tuple[tuple[str, ...], str, ZoneChoice]:
        """Return `(chosen sources, why, the new choice)` (D6 §5.7)."""
        cheapest = ranked[0]
        available = {row.load_id: row for row in ranked}
        current = self._choice
        incumbent = None if current is None else available.get(current.source)

        reason = "cheapest per kWh heat"
        candidate: str | None = None
        candidate_since: datetime | None = None
        primary = cheapest
        since = ctx.now

        if current is not None and incumbent is not None:
            if incumbent.load_id == cheapest.load_id:
                # Unchanged: the dwell keeps running from when it was chosen, or it
                # would be reset by every tick and never expire.
                since = current.since
            elif self._penalty_urgency(incumbent, cheapest, ctx):
                reason = "capacity penalty"
            elif self._may_switch(incumbent, cheapest, current, ctx.now):
                reason = "cheaper per kWh heat"
            else:
                primary = incumbent
                since = current.since
                reason = "dwell"
                candidate = cheapest.load_id
                candidate_since = self._candidate_since(cheapest.load_id, current, ctx.now)

        chosen = self._cover(primary, ranked)
        return (
            chosen,
            reason,
            ZoneChoice(
                source=primary.load_id,
                since=since,
                candidate=candidate,
                candidate_since=candidate_since,
            ),
        )

    def _cover(self, primary: _Ranked, ranked: tuple[_Ranked, ...]) -> tuple[str, ...]:
        """Return the primary source plus the cheapest top-ups it needs (D6 §5.7).

        A heat pump below −15 °C cannot carry the whole deficit on its own, and the
        answer is the next cheapest source beside it, not instead of it.
        """
        chosen = [primary.load_id]
        covered = primary.heat_w
        for row in ranked:
            if covered + 1e-6 >= self._demand_w:
                break
            if row.load_id == primary.load_id:
                continue
            chosen.append(row.load_id)
            covered += row.heat_w
        return tuple(chosen)

    def _penalty_urgency(self, incumbent: _Ranked, cheapest: _Ranked, ctx: AllocCtx) -> bool:
        """Whether the ceiling is buying the switch, dwell or no dwell (D6 §5.4).

        Stage 2's action is "substitution in". A 30-minute dwell would spend the
        window waiting, so the move **off** electricity is immediate; the move back
        is not, because the new source's own dwell starts now - which is what stops
        the pair flapping around the stage-2 threshold
        (`design/DECISIONS.md` D-0244).
        """
        return (
            ctx.stage >= PENALTY_FROM_STAGE
            and incumbent.source.carrier is Carrier.ELECTRICITY
            and cheapest.source.carrier is not Carrier.ELECTRICITY
        )

    def _may_switch(
        self, incumbent: _Ranked, cheapest: _Ranked, current: ZoneChoice | None, now: datetime
    ) -> bool:
        """Whether all three gates are open: margin, confirmation and dwell (§5.7)."""
        if current is None:
            return True
        if now - current.since < timedelta(minutes=self.min_dwell_min):
            return False
        # A margin on the absolute incumbent cost, so a negative price behaves
        # (0.85 × a negative number is *larger*, not cheaper - INV-51).
        margin = abs(incumbent.cost) * Decimal(str(self.switch_hysteresis))
        if incumbent.cost - cheapest.cost < margin:
            return False
        if current.candidate != cheapest.load_id or current.candidate_since is None:
            return False
        return now - current.candidate_since >= timedelta(seconds=self.switch_confirm_s)

    def _candidate_since(
        self, candidate: str, current: ZoneChoice | None, now: datetime
    ) -> datetime:
        """Return when this candidate first undercut the incumbent."""
        if current is not None and current.candidate == candidate and current.candidate_since:
            return current.candidate_since
        return now
