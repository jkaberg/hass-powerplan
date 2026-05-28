"""D6 §9 14 through the engine: the circuit's reading rides in `Inputs`.

The garage circuit - 32 A on three phases, the charger and the sauna behind it -
built from a `CircuitSpec` the way the runtime builds it from the subentry, its
sub-meter sampled into `Inputs.circuits` every tick. The charger is capped by
what the sub-meter leaves; a blind sub-meter falls back to the members' own
figures; a breach is a stage 4 for the members only, the site's stage untouched,
and one `breach` event with `breach = "circuit"` per edge (D6 §8, D8 §5.6).

The charger here obeys: each tick it draws what the last tick granted, and the
sub-meter reads the sauna, the charger and whatever else is plugged into the
garage - so the reading is consistent with the loads, as a real sub-meter is.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.allocation import CircuitSpec
from custom_components.powerplan.core.engine import (
    Engine,
    EngineState,
    EventKind,
    Knobs,
    LoadReads,
)
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.metering import MeterSample, Quality, Reading
from custom_components.powerplan.core.model import Mode
from custom_components.powerplan.core.tariffs import Target
from tests.core.engine.conftest import PROFILE, START, evaluator, inputs_at, site, window_meter
from tests.core.loads.conftest import ev_load, load_from, reads

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.model import Snapshot

#: The garage: 32 A on three phases of the 230 V IT supply.
GARAGE = CircuitSpec(
    key="garage", fuse_a=32.0, phases=3, members=frozenset({"ev", "sauna"}), sub_metered=True
)
GARAGE_W = GARAGE.limit_w(PROFILE)
SAUNA_W = 6000.0
#: The conftest's charger is single-phase: 230 W per amp, 32 A, a 6 A floor (D4 §5.11).
EV_W_PER_A = 230.0
EV_FLOOR_W = 6.0 * EV_W_PER_A
EV_MAX_W = 32.0 * EV_W_PER_A
TICK = timedelta(seconds=10.0)

#: A wide ceiling and both members forced: nothing but the circuit holds the charger.
KNOBS = Knobs(target=Target(kind="kw", kw=25.0), modes={"ev": Mode.FORCE, "sauna": Mode.FORCE})


def _engine() -> Engine:
    cfg = site()
    loads = (
        ev_load(strategy="always"),
        load_from("generic_switch", {"appliance": "sauna", "power_w": SAUNA_W}, load_id="sauna"),
    )
    return Engine(cfg, window_meter(cfg), evaluator(), loads, constraints=(GARAGE.limit(PROFILE),))


class _Garage:
    """The garage as the tick sees it: an obedient charger and sauna, and a sub-meter."""

    def __init__(self) -> None:
        self.engine = _engine()
        self.state = EngineState()
        self.at: datetime = START
        self.ev_w = 8.0 * EV_W_PER_A
        self.sauna_w = SAUNA_W
        #: What else is plugged into the garage that nobody meters.
        self.extra_w = 0.0
        #: `blind` publishes the sub-meter as unavailable; `age_s` back-dates it.
        self.blind = False
        self.age_s = 0.0

    def tick(self) -> tuple[Snapshot, Any]:
        """Run one tick over the garage's current state and let the charger obey."""
        at = self.at
        sub_w = self.sauna_w + self.ev_w + self.extra_w
        reading = (
            Reading(value=0.0, at=at, source="sub", quality=Quality.UNAVAILABLE)
            if self.blind
            else Reading(value=sub_w, at=at - timedelta(seconds=self.age_s), source="sub")
        )
        inputs = replace(
            inputs_at(
                site(),
                at,
                grid_w=1_200.0 + sub_w,
                loads={
                    "ev": LoadReads(
                        reads=reads(
                            at,
                            numbers={
                                Role.POWER: self.ev_w,
                                Role.CURRENT_SET: self.ev_w / EV_W_PER_A,
                                Role.SOC: 40.0,
                                Role.CURRENT_MAX: 32.0,
                            },
                            texts={Role.STATUS: "charging", Role.ENABLE: "on"},
                        )
                    ),
                    "sauna": LoadReads(
                        reads=reads(
                            at,
                            numbers={Role.POWER: self.sauna_w},
                            texts={Role.SWITCH: "on" if self.sauna_w > 0.0 else "off"},
                        )
                    ),
                },
                knobs=KNOBS,
            ),
            circuits={"garage": MeterSample(grid_w=reading)},
        )
        self.state, snapshot, effect = self.engine.tick(self.state, inputs)
        granted = snapshot.loads["ev"].granted_w
        self.ev_w = 0.0 if granted <= 0.0 else max(EV_FLOOR_W, granted)
        self.sauna_w = SAUNA_W if snapshot.loads["sauna"].granted_w > 0.0 else 0.0
        self.at = at + TICK
        return snapshot, effect

    def settle(self, ticks: int = 8) -> Snapshot:
        """Tick until the obedient charger and the sub-meter agree."""
        for _ in range(ticks):
            snapshot, _effect = self.tick()
        return snapshot


def _breaches(effect: Any) -> list[dict[str, Any]]:
    return [
        dict(event.data)
        for event in effect.ha_events
        if event.kind is EventKind.BREACH and event.data.get("breach") == "circuit"
    ]


@pytest.mark.inv("INV-60")
def test_the_sub_meter_in_inputs_caps_the_charger_under_the_garage_fuse() -> None:
    """The charger gets the fuse less what the sub-meter sees of everyone else."""
    garage = _Garage()
    snapshot = garage.settle()

    ev = snapshot.loads["ev"]
    assert "garage" in ev.capped_by, ev
    # 12.75 kW of fuse less 6 kW of sauna, quantised down to whole amps: 29 A.
    assert ev.granted_w == pytest.approx(29.0 * EV_W_PER_A)
    assert ev.granted_w < EV_MAX_W, "the circuit, not the charger's maximum, is what binds"
    row = snapshot.alloc.circuits["garage"]
    assert row.sub_meter is True
    assert row.limit_w == pytest.approx(GARAGE_W)
    assert row.measured_w == pytest.approx(SAUNA_W + 29.0 * EV_W_PER_A)
    assert row.breach is False
    assert row.members == ("ev", "sauna")


@pytest.mark.inv("INV-17")
@pytest.mark.inv("INV-60")
def test_a_blind_or_stale_sub_meter_falls_back_to_the_members() -> None:
    """Unavailable, or older than the stale cap, the reading is dropped: Σ members rules (D6 §8)."""
    garage = _Garage()
    garage.settle()
    garage.extra_w = 4_000.0

    garage.blind = True
    snapshot, _effect = garage.tick()
    row = snapshot.alloc.circuits["garage"]
    assert row.sub_meter is False
    # The heater nobody meters is invisible to the sum: the members alone are under the fuse.
    assert row.measured_w == pytest.approx(SAUNA_W + 29.0 * EV_W_PER_A)
    assert row.breach is False

    garage.blind = False
    garage.age_s = 600.0
    snapshot, _effect = garage.tick()
    row = snapshot.alloc.circuits["garage"]
    assert row.sub_meter is False, "a ten-minute-old reading may neither open the fuse nor slam it"
    assert row.breach is False


@pytest.mark.inv("INV-60")
def test_a_heater_the_charger_can_absorb_is_not_a_breach() -> None:
    """A 4 kW heater appears: the charger backs off to what is left, and no stage 4 fires.

    `post()` reads "what is still violated once the grants are decided" (D6 §2):
    the fuse at 130 % for the seconds the charger takes to back off is the
    grants' job, not the ladder's (D-0284).
    """
    garage = _Garage()
    garage.settle()
    garage.extra_w = 4_000.0

    events: list[dict[str, Any]] = []
    for _ in range(8):
        snapshot, effect = garage.tick()
        events.extend(_breaches(effect))

    assert events == []
    assert snapshot.ladder.stage == 0
    assert snapshot.alloc.circuits["garage"].breach is False
    ev = snapshot.loads["ev"]
    assert ev.stage == 0
    # 12.75 kW − 4 kW unseen − 6 kW of sauna = 2.75 kW → 11 A.
    assert ev.granted_w == pytest.approx(11.0 * EV_W_PER_A)
    assert snapshot.loads["sauna"].granted_w == pytest.approx(SAUNA_W)
    assert snapshot.alloc.circuits["garage"].measured_w <= GARAGE_W


@pytest.mark.inv("INV-60")
def test_a_circuit_breach_sheds_the_members_only_and_fires_one_event_per_edge() -> None:
    """An 8 kW guest car on the dumb socket: the members go to stage 4, the site stays at 0."""
    garage = _Garage()
    before = garage.settle()
    assert before.ladder.stage == 0

    # 8 kW nobody meters: even with the charger at its 6 A floor the sauna does
    # not fit beside it, so the grants cannot resolve the breach.
    garage.extra_w = 8_000.0
    during, effect = garage.tick()
    ev = during.loads["ev"]
    # A scoped breach is as blunt as the site's: the charger stops on the window
    # horizon's terms (44 min left, past the 10-minute guard), the sauna is off,
    # both shed for the circuit and at stage 4 (D6 §5.8, D-0284).
    assert ev.granted_w == 0.0
    assert ev.stage == 4
    assert ev.shed is True
    assert ev.shed_reason == "circuit"
    assert during.loads["sauna"].granted_w == 0.0
    assert during.loads["sauna"].shed_reason == "circuit"
    assert during.ladder.stage == 0, "the site's ladder is untouched by a circuit breach"
    assert during.alloc.circuits["garage"].breach is True
    events = _breaches(effect)
    assert len(events) == 1
    event = events[0]
    assert event["scope"] == "garage"
    # The event carries what the fuse physically saw over: the guest car, the
    # sauna and the charger at last tick's 29 A, less the fuse.
    assert event["excess_w"] == pytest.approx(8_000.0 + SAUNA_W + 29.0 * EV_W_PER_A - GARAGE_W)
    assert event["measured_w"] == pytest.approx(8_000.0 + SAUNA_W + 29.0 * EV_W_PER_A)
    assert set(event["members"]) == {"ev", "sauna"}
    assert {row["load"] for row in event["table"]} <= {"ev", "sauna"}

    # The sauna is off and the charger back at what the guest car leaves it; the
    # circuit settles under its fuse - and no second event on the way.
    events_after: list[dict[str, Any]] = []
    for _ in range(8):
        after, effect = garage.tick()
        events_after.extend(_breaches(effect))
    assert events_after == []
    assert after.alloc.circuits["garage"].breach is False
    assert after.loads["sauna"].granted_w == 0.0
    assert after.loads["ev"].granted_w == pytest.approx(20.0 * EV_W_PER_A)
    assert after.loads["ev"].stage == 0
    assert after.alloc.circuits["garage"].measured_w <= GARAGE_W
