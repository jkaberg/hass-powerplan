"""Prices: the four regimes, the DST slot counts, and determinism per seed."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.sim.base import QUARTER_S
from tests.sim.prices import (
    FLAT,
    MONTHLY_SPOT_NOK,
    NEGATIVE_DAYS,
    NEGATIVE_DEPTH_NOK,
    NORGESPRIS_NOK_PER_KWH,
    OUTAGE,
    SPOT_LIKE,
    PriceRegime,
    PriceSim,
)

OSLO = ZoneInfo("Europe/Oslo")
WINTER_DAY = date(2027, 1, 13)
DST_AUTUMN = date(2026, 10, 25)
DST_SPRING = date(2027, 3, 28)


def sim(seed: int = 5, kind: str = SPOT_LIKE) -> PriceSim:
    """Build a price source with one regime covering the whole test year."""
    return PriceSim(
        seed=seed,
        regimes=(PriceRegime(kind, date(2026, 1, 1), date(2028, 1, 1)),),
    )


def prices_of(source: PriceSim, day: date) -> list[float]:
    """Every price of the local day, with the outage case ruled out explicitly."""
    slots = source.slots(day)
    out = [s.nok_per_kwh for s in slots if s.nok_per_kwh is not None]
    assert len(out) == len(slots)
    return out


def test_flat_is_norgespris_in_every_slot() -> None:
    """Under Norgespris the energy-shift saving is exactly zero (HLD §8, PLAN R10)."""
    slots = sim(kind=FLAT).slots(WINTER_DAY)
    assert {s.nok_per_kwh for s in slots} == {NORGESPRIS_NOK_PER_KWH}


def test_an_outage_is_none_not_zero() -> None:
    """A missing price is missing; the synthesised floor is D1's job, not ours."""
    slots = sim(kind=OUTAGE).slots(WINTER_DAY)
    assert all(s.nok_per_kwh is None for s in slots)


def test_spot_like_has_a_night_trough_and_an_evening_peak() -> None:
    """A double-peaked Nordic day, and the night is where the loads want to go."""
    slots = sim().slots(WINTER_DAY)
    by_hour: dict[int, list[float]] = {}
    for slot in slots:
        assert slot.nok_per_kwh is not None
        by_hour.setdefault(slot.start.astimezone(OSLO).hour, []).append(slot.nok_per_kwh)

    means = {h: sum(v) / len(v) for h, v in by_hour.items()}
    night = sum(means[h] for h in (1, 2, 3, 4)) / 4.0
    evening = sum(means[h] for h in (17, 18, 19)) / 3.0
    assert evening > night


def test_the_monthly_level_follows_the_no3_table() -> None:
    """January is dearer than July because the NO3 statistics say so."""
    source = sim()
    january = [p for d in range(28) for p in prices_of(source, date(2027, 1, 1 + d))]
    july = [p for d in range(28) for p in prices_of(source, date(2027, 7, 1 + d))]
    assert sum(january) / len(january) > sum(july) / len(july)
    assert MONTHLY_SPOT_NOK[0] > MONTHLY_SPOT_NOK[6]


def test_a_negative_day_goes_below_zero_and_nothing_clamps_it() -> None:
    """INV-51: nothing in powerplan clamps a negative price, and nor does the fiction."""
    prices = prices_of(sim(kind=NEGATIVE_DAYS), date(2027, 4, 1))
    assert min(prices) == pytest.approx(-NEGATIVE_DEPTH_NOK, abs=1e-9)
    assert max(prices) > 0.0


@pytest.mark.parametrize(
    ("day", "expected"),
    [(WINTER_DAY, 96), (DST_AUTUMN, 100), (DST_SPRING, 92)],
)
def test_a_dst_day_has_92_or_100_quarter_slots(day: date, expected: int) -> None:
    """A slot's length is a property of the slot (D9 §5.3 `dst_autumn`/`dst_spring`)."""
    slots = sim().slots(day)
    assert len(slots) == expected
    assert all(s.seconds == QUARTER_S for s in slots)
    assert len({s.start for s in slots}) == expected
    assert slots == tuple(sorted(slots, key=lambda s: s.start))


def test_the_same_seed_gives_a_byte_identical_year_and_another_seed_does_not() -> None:
    """Two runs, same seed, byte-identical (D9 §9 item 8)."""

    def month(seed: int) -> str:
        source = sim(seed=seed)
        start = date(2027, 1, 1)
        return repr([source.slots(start + timedelta(days=d)) for d in range(31)])

    assert month(5) == month(5)
    assert month(5) != month(6)


def test_regimes_switch_on_the_local_date() -> None:
    """The year straddles the Norgespris end (D9 §5.9's `y2026_27`)."""
    source = PriceSim(
        seed=5,
        regimes=(
            PriceRegime(FLAT, date(2026, 7, 1), date(2027, 1, 1)),
            PriceRegime(SPOT_LIKE, date(2027, 1, 1), date(2027, 7, 1)),
        ),
    )
    assert source.kind_on(date(2026, 12, 31)) == FLAT
    assert source.kind_on(date(2027, 1, 1)) == SPOT_LIKE
    assert source.slots(date(2026, 12, 31))[0].nok_per_kwh == NORGESPRIS_NOK_PER_KWH
    assert source.slots(date(2027, 1, 1))[0].nok_per_kwh != NORGESPRIS_NOK_PER_KWH
