"""What a country module declares: VAT, levies, tax zones (D13 §5.1, §9, §9.1).

VAT and levies are national law, the same for every household in a zone, so they
ship here - dated, regional and sourced - and never in a household's copy (INV-70,
INV-71). A rate is looked up **for the slot's date** (D13 §18 G16): an announced
change (GB's zero rate from 2026-10-01, CY's 9 % ending) applies on its date
without a release, and a past month keeps the rate it was billed at.

Pure data and two lookups. The flow, the composer (D1 §5.3) and `spec()` read a
module; nothing here reads a household.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

__all__ = ["TEDB", "CountryModule", "Levy", "Rate", "Scheme", "Zone", "pick", "tedb"]

#: The Commission's Taxes in Europe Database - every EU row's cross-check (§9.1).
TEDB = "https://ec.europa.eu/taxation_customs/tedb/"
#: The day §9.1's table was read.
READ = date(2026, 9, 24)


@dataclass(frozen=True, slots=True)
class Rate:
    """One dated rate: a VAT fraction, or a levy in major units per kWh excl. VAT.

    `valid_from = None` is a rate in force before the first date the module tracks.
    `upto_kw` limits a rate to a contracted power at or below it - Spain's 10 %
    and the Canary Islands' IGIC 0 % for a home ≤ 10 kW. `until` is an end the law
    has announced with no successor written yet (IE's 9 % to 2030-12-31): the rate
    still answers after it, and `tools/preset_age.py` warns as it nears.
    """

    valid_from: date | None
    value: Decimal
    source: str
    verified: date = READ
    upto_kw: float | None = None
    until: date | None = None


def pick(rates: tuple[Rate, ...], day: date, contracted_kw: float | None = None) -> Rate | None:
    """Return the rate in force on `day`: the last one listed that has started.

    Rates are listed in date order; at the same date a conditional rate follows
    the one it narrows, so it wins where its `upto_kw` holds. Before the first
    start the earliest answers, as `TariffSpec.version_at` does. `None` only when
    no rate applies at all.
    """
    found: Rate | None = None
    for rate in rates:
        if rate.upto_kw is not None and (contracted_kw is None or contracted_kw > rate.upto_kw):
            continue
        if found is None or rate.valid_from is None or rate.valid_from <= day:
            found = rate
    return found


@dataclass(frozen=True, slots=True)
class Levy:
    """A per-kWh levy on the grid line (D13 §9): `forbruksavgift`, `energiskatt`."""

    key: str
    rates: tuple[Rate, ...]


@dataclass(frozen=True, slots=True)
class Zone:
    """A part of the country taxed differently (D13 §9.1).

    `vat` replaces the national rates where given; `levies` replaces a levy's rates
    by key - an exemption is a zero rate, not a missing levy, so the breakdown keeps
    its shape. `covers` names the regions it is, as the law names them (data).
    """

    key: str
    covers: str
    vat: tuple[Rate, ...] | None = None
    levies: tuple[Levy, ...] = ()
    #: What settles the zone from a postcode's place (D13 §6 step 0): a municipality
    #: number listed here, else its county's. Municipalities win over counties.
    municipalities: tuple[str, ...] = ()
    counties: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Scheme:
    """A state scheme the household may be in (D13 §4 party 3): a subsidy above a threshold.

    `threshold` is dated, excl. VAT; `share` what the state pays of the spot above it.
    `excludes` names the supplier kinds it cannot join (Norgespris: `state_fixed`).
    """

    key: str
    threshold: tuple[Rate, ...]
    share: Decimal
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CountryModule:
    """Everything national about one country (D13 §5.1, O21).

    `code` is ISO 3166-1 alpha-2, as `hass.config.country` holds it (`GB`, `GR`);
    `tedb` is the country's code in the Commission's TEDB (`EL`), `None` outside
    the EU. `vat = ()` is a country with no national rate (the US): the flow asks
    it (§9.1, step 1c-prime). `rule_template` is its file under `rules/`, if one ships.
    """

    code: str
    name: str
    currency: str
    vat: tuple[Rate, ...]
    tedb: str | None = None
    levies: tuple[Levy, ...] = ()
    zones: tuple[Zone, ...] = ()
    rule_template: str | None = None
    #: IANA zones in the country: the flow pre-selects the country from HA's zone (D13 step 0-prime).
    time_zones: tuple[str, ...] = ()
    #: The official directory a postcode is sent to (O17), `None` where there is none yet.
    postcode: str | None = None
    schemes: tuple[Scheme, ...] = ()

    def zone(self, key: str | None) -> Zone | None:
        """Return the zone `key`, or `None` for the national rates."""
        return next((zone for zone in self.zones if zone.key == key), None)

    def vat_at(
        self, day: date, zone: str | None = None, contracted_kw: float | None = None
    ) -> Decimal | None:
        """Return the VAT fraction on `day` in `zone`; `None` where the module has none."""
        found = self.zone(zone)
        rates = found.vat if found is not None and found.vat is not None else self.vat
        rate = pick(rates, day, contracted_kw)
        return None if rate is None else rate.value

    def levies_at(self, day: date, zone: str | None = None) -> dict[str, Decimal]:
        """Return each levy on `day` in `zone`, per kWh excl. VAT, in the module's order."""
        found = self.zone(zone)
        local = {levy.key: levy for levy in found.levies} if found is not None else {}
        amounts: dict[str, Decimal] = {}
        for levy in self.levies:
            rate = pick(local.get(levy.key, levy).rates, day)
            amounts[levy.key] = Decimal(0) if rate is None else rate.value
        return amounts

    def zone_of(self, municipality: str, county: str) -> str | None:
        """Return the tax zone a postcode's municipality settles, `None` for the national rates."""
        for zone in self.zones:
            if municipality in zone.municipalities:
                return zone.key
        return next((zone.key for zone in self.zones if county in zone.counties), None)

    def scheme(self, key: str) -> Scheme | None:
        """Return the scheme `key`, if the country has it."""
        return next((scheme for scheme in self.schemes if scheme.key == key), None)

    def dated(self) -> tuple[Rate, ...]:
        """Every rate the module ships - what `preset_age.py` ages (D13 §12.2)."""
        rates = [*self.vat, *(rate for levy in self.levies for rate in levy.rates)]
        rates += [rate for scheme in self.schemes for rate in scheme.threshold]
        for zone in self.zones:
            rates += [*(zone.vat or ()), *(rate for levy in zone.levies for rate in levy.rates)]
        return tuple(rates)


def tedb(percent: str, *, source: str = TEDB) -> tuple[Rate, ...]:
    """Return one national VAT rate in force on the day §9.1 was read, from TEDB."""
    return (Rate(None, Decimal(percent) / 100, source),)
