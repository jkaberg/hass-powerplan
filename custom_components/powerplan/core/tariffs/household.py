"""The household's price by party: grid company, supplier, state (D13 §3).

The site's **tariff copy** (HLD §2): what the grid company's source published -
capacity, the energy charge by time, fixed fees - kept with its `Basis` exactly
as published (INV-71); the supplier contract as the household described it; the
state's tax zone. It lives in `entry.data.tariff.price` and is the only tariff
the runtime reads (INV-66). VAT and levies are **not** in it: they are national
law and come from the country module at each slot's date (INV-70, §9.1), so a
rate change reaches every copy with the release that carries it.

Two derivations read it, never store anything: `spec()` here - D2's `TariffSpec`
with each fee as the household pays it - and the composer's chain by party, in
`core/pricing/party.py` (D1 §5.3), which strips the state's share out of a price
published with it and adds the zone's own back, once (INV-72).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from ..model import Money
from . import countries
from .model import (
    ContractedPower,
    HolidayMode,
    Linear,
    PeakTariff,
    StepTable,
    TariffRule,
    TariffSpec,
    TariffVersion,
    Tiers,
    TimeFilter,
)
from .rules import loader

__all__ = [
    "SCHEMA",
    "Basis",
    "EnergyPeriod",
    "EnergyVersion",
    "FeeVersion",
    "GridTariff",
    "HouseholdPrice",
    "Party",
    "Provenance",
    "StateTerms",
    "SupplierContract",
    "TaxZone",
    "energy_period",
    "fee_factor",
    "from_json",
    "levies_at",
    "published_levies_at",
    "published_vat_at",
    "spec",
    "to_json",
    "vat_at",
]

#: The copy's schema version in `entry.data.tariff.price` (D13 §3).
SCHEMA = 1

#: The override key for one levy standing in for all of a country's (an old `levy` add-on).
ALL_LEVIES = "levy"


class Party(StrEnum):
    """Who a price component belongs to (HLD §2, INV-72)."""

    GRID = "grid"
    SUPPLIER = "supplier"
    STATE = "state"


@dataclass(frozen=True, slots=True)
class Basis:
    """What a set of prices already includes, as published (D13 §3, §8)."""

    vat: bool
    levies: frozenset[str] = frozenset()


EXCL = Basis(vat=False)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where the grid company's copy came from (D13 §3, rule 4).

    `source` is the adapter key, or `shipped` (a WP4.6 copy, §10), `template`
    (a rule template filled from the bill), `custom` or `none` (a price-only site).
    """

    source: str
    url: str | None = None
    fetched: date | None = None
    attribution: str | None = None
    #: The source's tier (D13 §5.1), `None` for a copy no source fetched.
    tier: str | None = None


@dataclass(frozen=True, slots=True)
class EnergyPeriod:
    """One period of the grid's energy charge; `when = None` matches always."""

    when: TimeFilter | None
    price: Decimal
    name: str | None = None


@dataclass(frozen=True, slots=True)
class EnergyVersion:
    """The grid's energy charge from `valid_from` on: first matching period, else `fallback`."""

    valid_from: date
    periods: tuple[EnergyPeriod, ...]
    fallback: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class FeeVersion:
    """A fixed fee from `valid_from` on, per day, month or year (D13 §3, §18 G21).

    The bill only: a fixed fee never enters a plan (D13 §7).
    """

    valid_from: date
    amount: Decimal
    per: Literal["day", "month", "year"] = "month"

    def for_days(self, days: int) -> Decimal:
        """Return the fee for a month of `days` days: per day × days, else the month's share."""
        if self.per == "day":
            return self.amount * days
        if self.per == "year":
            return self.amount / 12
        return self.amount


@dataclass(frozen=True, slots=True)
class GridTariff:
    """Party 1 - the grid company's rules, as its source published them (D13 §3).

    `capacity` is D2's versions (rules and prices per validity); `energy` the
    grid's energy charge by time; `fixed_fee` the fees. All three share `basis`.
    The per-load tariffs, switched windows and feed-in terms of §3 are TS.7's and
    Phase 7's and join this type with them.
    """

    operator: str
    product: str | None
    provenance: Provenance
    currency: str
    basis: Basis
    capacity: tuple[TariffVersion, ...]
    energy: tuple[EnergyVersion, ...] = ()
    fixed_fee: tuple[FeeVersion, ...] = ()
    events: tuple[str, ...] = ()
    renew_at: date | None = None
    capacity_id: str = "copy"
    #: The last day the source publishes the copy for, where it says (`gyldig_til`).
    valid_to: date | None = None
    #: The source's own handles for the operator and the product, for the renewal (§10).
    operator_key: str | None = None
    product_key: str | None = None

    def energy_at(self, day: date) -> EnergyVersion | None:
        """Return the energy charge in force on `day`; before the first, the first."""
        return _at(self.energy, day)

    def capacity_at(self, day: date) -> TariffVersion:
        """Return the capacity version in force on `day`."""
        found = _at(self.capacity, day)
        assert found is not None  # a copy has at least one version (the schema)
        return found


@dataclass(frozen=True, slots=True)
class SupplierContract:
    """Party 2 - the household's agreement, asked (D13 §3).

    TS.1 carries the kind and the basis; the supplier's own components still
    travel as the household's add-ons in `prices.modifiers`, which the chain
    reads as this party's (D1 §5.3). Markup, fee, own time of use and tiers
    move into this type with the supplier step.
    """

    kind: Literal["spot", "fixed", "state_fixed", "total_entity"] = "spot"
    basis: Basis = EXCL
    provenance: Provenance = field(default_factory=lambda: Provenance("asked"))


@dataclass(frozen=True, slots=True)
class TaxZone:
    """Where the household is taxed (D13 §3): a country module's zone, `None` = national."""

    country: str
    key: str | None = None
    settled: Literal["postcode", "product", "regulator", "asked", "national"] = "national"


@dataclass(frozen=True, slots=True)
class StateTerms:
    """Party 3 - from the tax zone (D13 §3).

    `overrides` is a household that knows better (O4): `vat` (a VAT-registered
    farm sets 0; a country without a module states its rate here), a levy by its
    key, or `levy` for one amount standing in for all of them (an old add-on).
    """

    zone: TaxZone
    overrides: Mapping[str, Decimal] = field(default_factory=dict)
    schemes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HouseholdPrice:
    """The household's price by party (D13 §3)."""

    grid: GridTariff
    supplier: SupplierContract
    state: StateTerms
    confirmed: Mapping[str, Any] = field(default_factory=dict)


def _at[V: (EnergyVersion, FeeVersion, TariffVersion)](
    versions: tuple[V, ...], day: date
) -> V | None:
    found: V | None = versions[0] if versions else None
    for version in versions:
        if version.valid_from <= day:
            found = version
    return found


# --------------------------------------------------------------------------- #
# The state's rates on a date (D13 §9, §18 G16)
# --------------------------------------------------------------------------- #


def vat_at(state: StateTerms, day: date, contracted_kw: float | None = None) -> Decimal:
    """Return the VAT the household pays on `day`: its override, else its zone's.

    A country without a module and no stated rate pays none here - the flow asks
    the rate there (§9.1, step 1c-prime) and stores it as the override.
    """
    if "vat" in state.overrides:
        return state.overrides["vat"]
    module = countries.get(state.zone.country)
    rate = None if module is None else module.vat_at(day, state.zone.key, contracted_kw)
    return Decimal(0) if rate is None else rate


def published_vat_at(state: StateTerms, day: date) -> Decimal:
    """Return the VAT a price published incl. VAT holds: the country's national rate.

    A source publishes for the whole country, so its VAT-inclusive figure holds the
    national rate, not a zone's; where there is no module, the household typed the
    figure itself at its own rate.
    """
    module = countries.get(state.zone.country)
    rate = None if module is None else module.vat_at(day)
    return vat_at(state, day) if rate is None else rate


def levies_at(state: StateTerms, day: date) -> dict[str, Decimal]:
    """Return each levy the household pays on `day`, per kWh excl. VAT."""
    if ALL_LEVIES in state.overrides:
        return {ALL_LEVIES: state.overrides[ALL_LEVIES]}
    module = countries.get(state.zone.country)
    amounts = {} if module is None else module.levies_at(day, state.zone.key)
    return {key: state.overrides.get(key, amount) for key, amount in amounts.items()}


def published_levies_at(state: StateTerms, day: date, basis: Basis) -> Decimal:
    """Return the levies a price published with `basis` holds on `day`, excl. VAT."""
    module = countries.get(state.zone.country)
    if module is None:
        return Decimal(0)
    return sum(
        (amount for key, amount in module.levies_at(day).items() if key in basis.levies),
        Decimal(0),
    )


# --------------------------------------------------------------------------- #
# spec(): D2's TariffSpec with fees as the household pays them (D13 §3)
# --------------------------------------------------------------------------- #


def spec(price: HouseholdPrice) -> TariffSpec:
    """Return D2's spec: the grid's capacity, each fee at the household's VAT (D13 §3).

    A fee published without VAT gains the zone's; one published with the national
    rate trades it for the zone's (a Nord-Norge house pays Tensio's step ex VAT).
    Each version is taxed at its own `valid_from`'s rate.
    """
    grid = price.grid
    versions = tuple(_as_paid(version, price) for version in grid.capacity)
    return TariffSpec(
        id=grid.capacity_id,
        name=grid.product or grid.operator,
        versions=versions,
        currency=grid.currency,
        country=price.state.zone.country or None,
        operator=grid.operator,
        source_url=grid.provenance.url,
        verified=None if grid.provenance.fetched is None else grid.provenance.fetched.isoformat(),
        assumed=None if grid.provenance.fetched is not None else _assumed(grid),
    )


def _assumed(grid: GridTariff) -> str:
    """Return what a copy with no fetch date says of its numbers: its versions' own words."""
    said = next((version.assumed for version in grid.capacity if version.assumed), None)
    return said or f"the {grid.provenance.source} copy's numbers as stored"


def fee_factor(price: HouseholdPrice, day: date, contracted_kw: float | None = None) -> Decimal:
    """Return what turns a published fee into the paid one on `day` (1 when unchanged)."""
    paid = Decimal(1) + vat_at(price.state, day, contracted_kw)
    if not price.grid.basis.vat:
        return paid
    published = Decimal(1) + published_vat_at(price.state, day)
    return Decimal(1) if paid == published else paid / published


def _as_paid(version: TariffVersion, price: HouseholdPrice) -> TariffVersion:
    contracted = version.contracted
    kw = None if contracted is None else max(limit.limit_kw for limit in contracted.limits)
    factor = fee_factor(price, version.valid_from, kw)
    if factor == 1:
        return version
    return replace(version, rules=tuple(_scaled(rule, factor) for rule in version.rules))


def _money(amount: Money, factor: Decimal) -> Money:
    return Money(amount.amount * factor, amount.currency)


def _scaled(rule: TariffRule, factor: Decimal) -> TariffRule:
    if isinstance(rule, ContractedPower):
        if rule.surcharge_per_kw is None:
            return rule
        return replace(rule, surcharge_per_kw=_money(rule.surcharge_per_kw, factor))
    if not isinstance(rule, PeakTariff):
        return rule
    pricing = rule.pricing
    scaled: StepTable | Linear | Tiers
    if isinstance(pricing, StepTable):
        scaled = replace(
            pricing,
            steps=tuple(
                replace(step, fee_per_period=_money(step.fee_per_period, factor))
                for step in pricing.steps
            ),
        )
    elif isinstance(pricing, Linear):
        scaled = replace(pricing, price_per_kw=_money(pricing.price_per_kw, factor))
    else:
        scaled = Tiers(bands=tuple((upto, _money(band, factor)) for upto, band in pricing.bands))
    return replace(rule, pricing=scaled)


# --------------------------------------------------------------------------- #
# JSON, as stored in `entry.data.tariff.price` (schema-versioned)
# --------------------------------------------------------------------------- #


def to_json(price: HouseholdPrice) -> dict[str, Any]:
    """Return the copy as plain JSON; money as strings, so it never passes a float."""
    grid = price.grid
    return {
        "schema": SCHEMA,
        "grid": {
            "operator": grid.operator,
            "product": grid.product,
            "provenance": _provenance_json(grid.provenance),
            "basis": _basis_json(grid.basis),
            "capacity": loader.dump(
                TariffSpec(
                    id=grid.capacity_id,
                    name=grid.product or grid.operator,
                    versions=grid.capacity,
                    currency=grid.currency,
                    # Each version carries its own `verified` or `assumed`; one here
                    # would be inherited by every version that has neither.
                    verified=None,
                    assumed=None,
                )
            ),
            "energy": [
                {
                    "valid_from": version.valid_from.isoformat(),
                    "fallback": str(version.fallback),
                    "periods": [_period_json(period) for period in version.periods],
                }
                for version in grid.energy
            ],
            "fixed_fee": [
                {
                    "valid_from": fee.valid_from.isoformat(),
                    "amount": str(fee.amount),
                    "per": fee.per,
                }
                for fee in grid.fixed_fee
            ],
            "events": list(grid.events),
            "renew_at": None if grid.renew_at is None else grid.renew_at.isoformat(),
            "valid_to": None if grid.valid_to is None else grid.valid_to.isoformat(),
            "operator_key": grid.operator_key,
            "product_key": grid.product_key,
        },
        "supplier": {
            "kind": price.supplier.kind,
            "basis": _basis_json(price.supplier.basis),
            "provenance": _provenance_json(price.supplier.provenance),
        },
        "state": {
            "zone": {
                "country": price.state.zone.country,
                "key": price.state.zone.key,
                "settled": price.state.zone.settled,
            },
            "overrides": {key: str(value) for key, value in price.state.overrides.items()},
            "schemes": list(price.state.schemes),
        },
        "confirmed": dict(price.confirmed),
    }


def from_json(raw: Mapping[str, Any]) -> HouseholdPrice:
    """Read a stored copy back; the capacity goes through the rule loader's validation."""
    if raw.get("schema") != SCHEMA:
        msg = f"tariff copy schema {raw.get('schema')!r}, expected {SCHEMA}"
        raise loader.PresetError(msg)
    grid = raw["grid"]
    capacity = loader.from_raw(grid["capacity"], source="entry.tariff.price")
    state = raw["state"]
    zone = state["zone"]
    supplier = raw.get("supplier") or {}
    return HouseholdPrice(
        grid=GridTariff(
            operator=grid["operator"],
            product=grid.get("product"),
            provenance=_provenance(grid["provenance"]),
            currency=capacity.currency,
            basis=_basis(grid["basis"]),
            capacity=capacity.versions,
            energy=tuple(
                EnergyVersion(
                    valid_from=date.fromisoformat(version["valid_from"]),
                    periods=tuple(_period(period) for period in version["periods"]),
                    fallback=Decimal(str(version.get("fallback") or 0)),
                )
                for version in grid.get("energy") or ()
            ),
            fixed_fee=tuple(
                FeeVersion(
                    valid_from=date.fromisoformat(fee["valid_from"]),
                    amount=Decimal(str(fee["amount"])),
                    per=fee.get("per", "month"),
                )
                for fee in grid.get("fixed_fee") or ()
            ),
            events=tuple(grid.get("events") or ()),
            renew_at=None if not grid.get("renew_at") else date.fromisoformat(grid["renew_at"]),
            capacity_id=capacity.id,
            valid_to=None if not grid.get("valid_to") else date.fromisoformat(grid["valid_to"]),
            operator_key=grid.get("operator_key"),
            product_key=grid.get("product_key"),
        ),
        supplier=SupplierContract(
            kind=supplier.get("kind", "spot"),
            basis=_basis(supplier.get("basis") or {"vat": False}),
            provenance=_provenance(supplier.get("provenance") or {"source": "asked"}),
        ),
        state=StateTerms(
            zone=TaxZone(
                country=zone.get("country") or "",
                key=zone.get("key"),
                settled=zone.get("settled", "national"),
            ),
            overrides={
                key: Decimal(str(value)) for key, value in (state.get("overrides") or {}).items()
            },
            schemes=tuple(state.get("schemes") or ()),
        ),
        confirmed=dict(raw.get("confirmed") or {}),
    )


def _basis_json(basis: Basis) -> dict[str, Any]:
    return {"vat": basis.vat, "levies": sorted(basis.levies)}


def _basis(raw: Mapping[str, Any]) -> Basis:
    return Basis(vat=bool(raw["vat"]), levies=frozenset(raw.get("levies") or ()))


def _provenance_json(provenance: Provenance) -> dict[str, Any]:
    return {
        "source": provenance.source,
        "url": provenance.url,
        "fetched": None if provenance.fetched is None else provenance.fetched.isoformat(),
        "attribution": provenance.attribution,
        "tier": provenance.tier,
    }


def _provenance(raw: Mapping[str, Any]) -> Provenance:
    fetched = raw.get("fetched")
    return Provenance(
        source=raw["source"],
        url=raw.get("url"),
        fetched=None if not fetched else date.fromisoformat(fetched),
        attribution=raw.get("attribution"),
        tier=raw.get("tier"),
    )


def _period_json(period: EnergyPeriod) -> dict[str, Any]:
    raw: dict[str, Any] = {"price": str(period.price)}
    if period.name is not None:
        raw["name"] = period.name
    when = period.when
    if when is not None:
        raw["holidays"] = when.holidays.value
        for key in ("months", "weekdays"):
            if getattr(when, key) is not None:
                raw[key] = list(getattr(when, key))
        if when.hours is not None:
            raw["hours"] = [list(pair) for pair in when.hours]
    return raw


def energy_period(raw: Mapping[str, Any]) -> EnergyPeriod:
    """Return one period from a preset's or a copy's flat record (`hours`, `months`, …)."""
    return _period(raw)


def _period(raw: Mapping[str, Any]) -> EnergyPeriod:
    fields_ = ("hours", "weekdays", "months")
    when = None
    if any(raw.get(key) is not None for key in fields_) or raw.get("holidays") not in (
        None,
        HolidayMode.IGNORE.value,
    ):
        hours = raw.get("hours")
        when = TimeFilter(
            months=None if raw.get("months") is None else tuple(int(m) for m in raw["months"]),
            weekdays=(
                None if raw.get("weekdays") is None else tuple(int(d) for d in raw["weekdays"])
            ),
            hours=None if hours is None else tuple((int(a), int(b)) for a, b in hours),
            holidays=HolidayMode(raw.get("holidays", HolidayMode.IGNORE.value)),
        )
    return EnergyPeriod(when=when, price=Decimal(str(raw["price"])), name=raw.get("name"))


# --------------------------------------------------------------------------- #
# From a rule file or a WP4.6 copy (D13 §10)
# --------------------------------------------------------------------------- #


def from_preset(
    raw: Mapping[str, Any],
    *,
    source: str,
    zone: TaxZone,
    supplier: SupplierContract | None = None,
    typed: bool = False,
) -> HouseholdPrice:
    """Return the copy a rule file, a filled template or a WP4.6 copy becomes (D13 §10).

    The grid's energy charge moves out of the versions' `energy_components` into
    `energy`; the `includes` flag D-0523 put there becomes the copy's `Basis` - a
    `levy` included is every levy of the country's module. Figures the household
    `typed` (a template filled from the bill, `custom`) are incl. VAT (INV-71).
    """
    built = loader.from_raw(raw, source=source)
    module = countries.get(zone.country)
    energy: list[EnergyVersion] = []
    includes: set[str] = set()
    for version in built.versions:
        components = version.energy_components
        includes |= set(components.get("includes") or ())
        periods = (components.get("tou_schedule") or {}).get("periods")
        if periods:
            energy.append(
                EnergyVersion(
                    valid_from=version.valid_from,
                    periods=tuple(_period(period) for period in periods),
                )
            )
    levies = frozenset(levy.key for levy in module.levies) if module is not None else frozenset()
    basis = (
        Basis(vat=True)
        if typed
        else Basis(vat="vat" in includes, levies=levies if "levy" in includes else frozenset())
    )
    return HouseholdPrice(
        grid=GridTariff(
            operator=built.operator or built.name,
            product=built.name,
            provenance=Provenance(source=source, url=built.source_url),
            currency=built.currency,
            basis=basis,
            capacity=tuple(replace(version, energy_components={}) for version in built.versions),
            energy=tuple(energy),
            capacity_id=built.id,
        ),
        supplier=supplier or SupplierContract(),
        state=StateTerms(zone=zone),
    )
