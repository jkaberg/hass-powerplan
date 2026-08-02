"""D6 §5.8's external limit, wired to a real tick's own events (INV-1).

The constraint's own arithmetic - the cap, the blunt violation, `P_hard`'s
`min` - is already proven against hand-built objects
(`tests/core/allocation/test_17_external_limit.py`). What is new here is the
bridge: `Engine._external_limits` reads `Inputs.events` (D1's own
`EventStore`, handed in fresh each tick - nothing structural, the same
"not a subentry" precedent as a zone or a cycle reservation) and turns each
active `load_limit` announcement into one `ExternalLimit` per tick.

A forced charger, not a floor loop: a `SETPOINT` kind (D4 §5.4) is a
temperature write, not a wattage throttle, so a comfort-violated loop draws
its modelled nameplate however the grant is capped (it is the *grant*, not
the device, that item 1 bounds - D6 §5.3's own comfort note). An EV's
`MODULATE` kind obeys the grant in amps, the same choice D6 §9 17 itself
makes, so it is what proves the cap reaches a real device.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from custom_components.powerplan.core.engine import EngineState, Knobs, LoadReads
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.model import Mode
from custom_components.powerplan.core.pricing import Event, EventKind
from custom_components.powerplan.core.tariffs import Target
from tests.core.engine.conftest import START, engine_for, inputs_at, site
from tests.core.loads.conftest import ev_load, reads

#: D1 §14a's own example, well under the charger's 32 A / 7 360 W nameplate.
EVENT_W = 4200.0

#: Forces the charger's own want to its nameplate, whatever the strategy says.
KNOBS = Knobs(target=Target(kind="kw", kw=25.0), modes={"ev": Mode.FORCE})


def _charging_reads() -> dict[str, object]:
    return {
        "ev": LoadReads(
            reads=reads(
                START,
                numbers={Role.CURRENT_SET: 0.0, Role.SOC: 40.0, Role.CURRENT_MAX: 32.0},
                texts={Role.STATUS: "charging", Role.ENABLE: "on"},
            )
        )
    }


def _event(
    *,
    kind: EventKind = EventKind.LOAD_LIMIT,
    loads: tuple[str, ...] = ("ev",),
    active: bool = True,
    max_w: float = EVENT_W,
) -> Event:
    """Return one D1 announcement; `active=False` ends it a minute before `START`."""
    end = START + timedelta(hours=1) if active else START - timedelta(minutes=1)
    return Event(
        id="dso-1",
        source="test",
        kind=kind,
        start=START - timedelta(minutes=1),
        end=end,
        issued_at=START - timedelta(hours=1),
        valid_until=START + timedelta(hours=2),
        payload={"loads": list(loads), "max_w": max_w},
    )


def _tick(events: tuple[Event, ...]) -> object:
    cfg = site()
    engine = engine_for((ev_load(strategy="always"),), cfg=cfg)
    inputs = replace(
        inputs_at(cfg, START, grid_w=0.0, loads=_charging_reads(), knobs=KNOBS),
        events=events,
    )
    _state, snapshot, _effects = engine.tick(EngineState(), inputs)
    return snapshot


def test_an_active_load_limit_event_caps_the_named_load() -> None:
    """A `load_limit` announcement for "ev" binds it to `max_w`, not its nameplate."""
    snapshot = _tick((_event(),))

    assert snapshot.loads["ev"].granted_w <= EVENT_W + 1e-6


def test_no_events_leaves_the_load_at_its_uncapped_want() -> None:
    """The baseline: absent any event, the forced charger is granted above the cap."""
    snapshot = _tick(())

    assert snapshot.loads["ev"].granted_w > EVENT_W


def test_an_ended_event_is_not_a_latch() -> None:
    """An event whose window has closed constrains nothing this tick (D1 §2)."""
    snapshot = _tick((_event(active=False),))

    assert snapshot.loads["ev"].granted_w > EVENT_W


def test_a_site_wide_event_caps_even_an_unnamed_load() -> None:
    """No `loads` named means every load - the same site-wide shape D6 §9 17 proves."""
    snapshot = _tick((_event(loads=()),))

    assert snapshot.loads["ev"].granted_w <= EVENT_W + 1e-6


def test_an_event_of_a_different_kind_is_not_a_load_limit() -> None:
    """Only `EventKind.LOAD_LIMIT` becomes an `ExternalLimit`; a reward is D1's own job."""
    snapshot = _tick((_event(kind=EventKind.REWARD),))

    assert snapshot.loads["ev"].granted_w > EVENT_W
