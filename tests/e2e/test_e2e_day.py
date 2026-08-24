"""D9 §9 10 - the `e2e` day: the real integration, the real flow, the simulated house.

One day of the benchmark year - 2027-01-13, a Wednesday, D9 §5.11's `e2e` span - runs
against `fake_house` in 10 s steps under the `freezer`. The site is created through
`hass.config_entries.flow` exactly as a household would create it (the meter picked from
the captured AMS device, Nord Pool suggested, Tensio at the 5–10 kW step, observe), and
nothing is injected. Every load of the house is passive: phase 1 is observe-only, and
the load subentries come later, so what is measured here is the meter, the prices, the
warnings and the wiring, and what is compared with the pure runner is the metering.

Along the way the meter goes silent for twenty minutes (a repair appears and
clears), the entry is unloaded and set up again mid-window (the store round
trip, INV-14), and the market publishes tomorrow at 13:00 CET (the publication
timer, INV-6). At the end the closed windows, `over_target`, the capacity fee
and the writes are compared with the pure runner's for the same day, and the
phase-1 gate's two numbers are measured: the window projection against the
simulated meter, and the lead of every peak warning.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan import events
from custom_components.powerplan.const import (
    CONF_ACTIVE,
    CONF_PATH,
    CONF_TARIFF,
    CONF_TIMEZONE,
    DOMAIN,
)
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.core.metering import ClosedWindow
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.tariffs import Evaluator
from custom_components.powerplan.core.tariffs.evaluator import Period
from custom_components.powerplan.core.tariffs.presets import loader
from custom_components.powerplan.entity import unique_id
from tests.benchmark.year import y2026_27
from tests.builders.houses import OSLO, nordic_detached
from tests.e2e.fake_house import FakeHouse
from tests.flows.test_site_flow import (
    ELECTRICAL_NO,
    _answer,
    _configure,
    _followups,
    _start,
    _tail,
    _through_prices,
)
from tests.runtime.conftest import restart_entry
from tests.scenarios.runner import TICK_S, Fault, Scenario, run_scenario
from tests.surface.test_entities import SITE_ENTITIES

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.config_entries import ConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.builders.houses import House
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.e2e

_LOGGER = logging.getLogger(__name__)

#: D9 §5.11: the `e2e` tier is one day, 2027-01-13. The first tick is 00:00:17
#: local (never a round minute, INV-43); 8 639 further steps end at 00:00:07.
DAY = date(2027, 1, 13)
SEED = 20260919
DAY_START = datetime.combine(DAY, time(0, 0, 17), tzinfo=OSLO)
STEPS = int(86400 / TICK_S) - 1
#: The ceiling both sides defend: the runner's 10 kW is Tensio's 5–10 kW step
#: (`step_2` in the flow's target select).
TARGET_KW = 10.0
TARGET_OPTION = "step_2"
#: The meter goes silent at 10:05 for twenty minutes; the repair needs ten (D8 §5.9).
OUTAGE_AT = DAY_START.replace(hour=10, minute=5)
OUTAGE_S = 1200.0
CHECK_STALE_AT = DAY_START.replace(hour=10, minute=20)
CHECK_FRESH_AT = DAY_START.replace(hour=10, minute=35)
#: Home Assistant restarts in the middle of the 12:00 window.
RESTART_AT = DAY_START.replace(hour=12, minute=40)
#: Either side of Nord Pool's 13:00 CET publication.
BEFORE_PUBLICATION = DAY_START.replace(hour=12, minute=0)
AFTER_PUBLICATION = DAY_START.replace(hour=14, minute=0)
PROBES = (
    DAY_START + timedelta(seconds=TICK_S),
    CHECK_STALE_AT,
    CHECK_FRESH_AT,
    BEFORE_PUBLICATION,
    AFTER_PUBLICATION,
)
#: PLAN §3, the phase-1 gate: the window projection against the simulated meter
#: at p95 over the last quarter of every window, and the warning lead.
PROJECTION_P95_KWH = 0.3
LAST_QUARTER_H = 0.25
WARNING_LEAD = timedelta(minutes=20)
PEAK_KINDS = frozenset({"peak", "peak_uncontrolled"})


def _house() -> House:
    """Return the benchmark house on the benchmark year, every load passive (D9 §9 11)."""
    year = y2026_27(SEED)
    house = nordic_detached(
        seed=SEED,
        start=year.start,
        price_regimes=year.price_regimes,
        weather_events=year.weather_events,
        controlled=frozenset(),
    )
    # The HA side runs what the flow picks, the shipped `no/tensio-ts`; the
    # benchmark's synthetic 2027 version is a test fixture neither side ships
    # (D-0524), so the pure side bills on the same file.
    return replace(
        house, tariff=Evaluator(loader.load("no/tensio-ts"), tz=OSLO, calendar=NO_HOLIDAYS)
    )


@pytest.fixture(scope="module")
def pure() -> ScenarioResult:
    """Run the same day through the pure runner: the same house, faults and ceiling."""
    return run_scenario(
        Scenario(
            name="e2e_day",
            house=_house,
            start=DAY_START,
            days=1.0,
            target_kw=TARGET_KW,
            faults=(
                Fault(kind="meter_stale", at=OUTAGE_AT, seconds=OUTAGE_S),
                Fault(kind="restart", at=RESTART_AT),
            ),
        )
    )


# --------------------------------------------------------------------------- #
# The flow, as a household drives it
# --------------------------------------------------------------------------- #


async def _through_meter(
    hass: HomeAssistant, result: dict[str, Any], device_id: str
) -> dict[str, Any]:
    """Electrical, the AMS device, and its roles - without the meter's own hour accumulator.

    The captured AMS publishes `total_increasing` kWh for the running hour with
    no `last_reset`; the pure runner feeds power and the register alone, so the
    site here binds the same two (plus the export register the meter has).
    """
    assert result["step_id"] == "meter"
    result = await _answer(hass, result, device=device_id)
    assert result["step_id"] == "meter_confirm"
    result = await _answer(hass, result, confirm="change")
    assert result["step_id"] == "meter_roles"
    roles = dict(result["data_schema"]({}))
    roles.pop("meter_window", None)
    result = await _answer(hass, result, **roles)
    assert result["step_id"] == "electrical"
    return await _answer(hass, result, **ELECTRICAL_NO)


async def _through_tariff(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Tensio, and the 5–10 kW step as the target."""
    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="no/tensio-ts")
    assert result["step_id"] == "tariff_preset"
    result = await _answer(hass, result, confirm="yes")
    assert result["step_id"] == "tariff_target"
    options = [row["value"] for row in result["data_schema"].schema["target"].config["options"]]
    assert TARGET_OPTION in options, options
    return await _answer(hass, result, target=TARGET_OPTION, risk="free_ride")


async def _create_site(hass: HomeAssistant, ams_meter: str, nordpool_entry: str) -> ConfigEntry:
    """Create the site through the full onboarding path, in observe."""
    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _followups(hass, result)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    return entry


# --------------------------------------------------------------------------- #
# What the day leaves behind
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Tick:
    """What one published snapshot said about the window and the warnings."""

    at: datetime
    trigger: str
    window_start: datetime
    used_kwh: float
    t_rem_h: float
    projected_kwh: float | None
    ceiling_kwh: float | None
    closed: tuple[ClosedWindow, ...]
    warned: frozenset[datetime]
    warned_ceilings: tuple[float, ...]
    reasons: int


class Recorder:
    """Every coordinator publish and every `powerplan_*` bus event."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Arm the bus listeners before the site exists."""
        self.ticks: list[Tick] = []
        self.events: list[Event[Any]] = []
        #: Ticks that ran before a listener could attach (startup, the first fetch).
        self.unseen = 0
        for kind in EventKind:
            hass.bus.async_listen(events.event_name(kind), self._on_event)

    @callback
    def _on_event(self, event: Event[Any]) -> None:
        self.events.append(event)

    def attach(self, runtime: Runtime) -> None:
        """Record the runtime's snapshots from now on; count the ones already run."""
        self.unseen += runtime.ticks

        @callback
        def on_update() -> None:
            snapshot = runtime.snapshot
            if snapshot is None or snapshot.meter is None:
                return
            peaks = [
                w for w in snapshot.warnings if w.kind in PEAK_KINDS and w.window_start is not None
            ]
            self.ticks.append(
                Tick(
                    at=snapshot.at,
                    trigger=snapshot.site.trigger,
                    window_start=snapshot.meter.window_start_utc,
                    used_kwh=snapshot.meter.used_kwh,
                    t_rem_h=snapshot.meter.t_rem_h,
                    projected_kwh=None
                    if snapshot.budget is None
                    else snapshot.budget.projected_kwh,
                    ceiling_kwh=None if snapshot.budget is None else snapshot.budget.ceiling_kwh,
                    closed=snapshot.meter.closed,
                    warned=frozenset(w.window_start for w in peaks),  # type: ignore[misc]
                    warned_ceilings=tuple(w.ceiling_kwh for w in peaks if w.kind == "peak"),
                    reasons=len(snapshot.reasons),
                )
            )

        runtime.coordinator.async_add_listener(on_update)

    def kinds(self) -> list[str]:
        """Return the event kinds, in order."""
        return [str(event.data["kind"]) for event in self.events]

    def of(self, kind: EventKind) -> list[dict[str, Any]]:
        """Return the payloads of one kind."""
        return [dict(event.data) for event in self.events if event.data["kind"] == kind.value]


def _entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Every site entity D8 §5.5 names, resolved from the registry (INV-50)."""
    registry = er.async_get(hass)
    out: dict[str, str] = {}
    for platform, key, _category, _enabled in SITE_ENTITIES:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id(entry.entry_id, key))
        assert entity_id is not None, f"{platform}.{key} was not created by the flow-made site"
        out[key] = entity_id
    return out


def _probe(hass: HomeAssistant, entry: ConfigEntry, ids: dict[str, str]) -> dict[str, Any]:
    """Return what a household would see at this instant: a few states and the repairs."""
    issues = ir.async_get(hass)

    def state_of(key: str) -> str | None:
        state = hass.states.get(ids[key])
        return None if state is None else state.state

    return {
        "meter_stale": state_of("meter_stale"),
        "meter_stale_issue": issues.async_get_issue(DOMAIN, f"{entry.entry_id}_meter_stale")
        is not None,
        "store_reset_issue": issues.async_get_issue(DOMAIN, f"{entry.entry_id}_store_reset")
        is not None,
        "prices_tomorrow": state_of("prices_tomorrow"),
        "price": state_of("price"),
        "presence": state_of("presence"),
        "window_used": state_of("window_used"),
    }


def _window_of(runtime: Runtime) -> tuple[datetime, float]:
    """Return the runtime's current window start and its `used_kwh`."""
    snapshot = runtime.snapshot
    assert snapshot is not None
    assert snapshot.meter is not None
    return snapshot.meter.window_start_utc, snapshot.meter.used_kwh


def _trace(
    ticks: list[Tick], start: datetime, every: timedelta = timedelta(minutes=5)
) -> list[str]:
    """Return one window's projection every few minutes, for a failure message."""
    rows: list[str] = []
    last: datetime | None = None
    for tick in ticks:
        if tick.window_start != start or (last is not None and tick.at - last < every):
            continue
        last = tick.at
        warned = sorted(w.astimezone(OSLO).strftime("%H:%M") for w in tick.warned)
        rows.append(
            f"{tick.at.astimezone(OSLO):%H:%M:%S} used={tick.used_kwh:.2f} "
            f"proj={tick.projected_kwh} ceil={tick.ceiling_kwh} warned={warned}"
        )
    return rows


def _windows(ticks: list[Tick]) -> list[ClosedWindow]:
    return [window for tick in ticks for window in tick.closed]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def _projection_errors(ticks: list[Tick], windows: list[ClosedWindow]) -> list[float]:
    """|projected − final| over the last quarter of every closed window."""
    final = {window.start_utc: window.kwh for window in windows}
    return [
        abs(tick.projected_kwh - final[tick.window_start])
        for tick in ticks
        if tick.projected_kwh is not None
        and tick.window_start in final
        and tick.t_rem_h <= LAST_QUARTER_H
    ]


def _energy_until(truth: list[tuple[datetime, float]], instant: datetime) -> float:
    """Return the simulated meter's true register at `instant` (a step covers `[now, now + 10 s)`)."""
    value = truth[0][1]
    for now, kwh in truth:
        if now + timedelta(seconds=TICK_S) > instant:
            break
        value = kwh
    return value


def _crossing(
    truth: list[tuple[datetime, float]], start: datetime, end: datetime
) -> datetime | None:
    """When the window's true energy reached the ceiling, if it did."""
    at_start = _energy_until(truth, start)
    for now, kwh in truth:
        if now < start or now >= end:
            continue
        if kwh - at_start >= TARGET_KW:
            return now + timedelta(seconds=TICK_S)
    return None


def _leads(
    ticks: list[Tick], windows: list[ClosedWindow], truth: list[tuple[datetime, float]]
) -> dict[datetime, tuple[timedelta | None, timedelta | None]]:
    """Per peak: (lead to the crossing, lead to the window start), `None` when never warned.

    A peak is a window that raises the period's metric - under Tensio's per-day
    maximum, the day's highest hour so far above the target. A later window over
    the target but under that maximum rides for free (PLAN §7 dec. 18): the
    budget's ceiling follows it, nothing is warned because nothing can be
    saved, and it is not a peak here either.
    """
    first_seen: dict[datetime, datetime] = {}
    for tick in ticks:
        for start in tick.warned:
            first_seen.setdefault(start, tick.at)
    out: dict[datetime, tuple[timedelta | None, timedelta | None]] = {}
    day_max: dict[date, float] = {}
    for window in windows:
        local_day = window.start_utc.astimezone(OSLO).date()
        ceiling = max(TARGET_KW, day_max.get(local_day, 0.0))
        day_max[local_day] = max(day_max.get(local_day, 0.0), window.kwh)
        if window.kwh <= ceiling + 1e-9:
            continue
        end = window.start_utc + timedelta(minutes=window.window_min)
        crossing = _crossing(truth, window.start_utc, end)
        seen = first_seen.get(window.start_utc)
        if seen is None or crossing is None:
            out[window.start_utc] = (None, None)
            continue
        out[window.start_utc] = (crossing - seen, window.start_utc - seen)
    return out


# --------------------------------------------------------------------------- #
# The day
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-6")
@pytest.mark.inv("INV-14")
@pytest.mark.inv("INV-44")
@pytest.mark.inv("INV-50")
async def test_10_the_e2e_day(  # noqa: PLR0915, PLR0917 - the day is one story; its fixtures are its cast
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    pure: ScenarioResult,
    record_property: Callable[[str, object], None],
) -> None:
    """D9 §9 10: the flow-made site, every entity, the day, the store, the events, the gate."""
    freezer.move_to(DAY_START)
    # Every step moves the frozen monotonic clock ten seconds at once, which
    # asyncio's debug mode would report as a slow callback 8 640 times.
    hass.loop.slow_callback_duration = 86_400.0
    await hass.config.async_update(time_zone=OSLO.key)
    _configure(hass, OSLO.key)
    house = _house()
    house.meter.inject_outage(OUTAGE_AT, OUTAGE_S)
    fake = FakeHouse(hass, house, DAY_START)
    fake.install()
    await hass.async_block_till_done()
    recorder = Recorder(hass)

    started = os.times().elapsed  # the freezer owns every clock in `time`; this one it does not
    entry = await _create_site(hass, ams_meter, nordpool_entry)
    runtime: Runtime = entry.runtime_data
    recorder.attach(runtime)
    assert entry.data[CONF_PATH] == "full"
    assert entry.data[CONF_ACTIVE] is False, "phase 1 starts in observe"
    assert entry.data[CONF_TIMEZONE] == OSLO.key
    assert entry.data[CONF_TARIFF]["target"] == TARGET_OPTION
    ids = _entity_ids(hass, entry)

    probes: dict[datetime, dict[str, Any]] = {}
    continuity: dict[str, Any] = {}
    ticks_before_restart = 0

    async def restart() -> None:
        nonlocal runtime, ticks_before_restart
        continuity["before"] = _window_of(runtime)
        ticks_before_restart = runtime.ticks
        await restart_entry(hass, entry, hass_storage)
        runtime = entry.runtime_data
        recorder.attach(runtime)
        continuity["after"] = _window_of(runtime)

    for _ in range(STEPS):
        due = fake.now + timedelta(seconds=TICK_S)
        await fake.advance(freezer, restart if due == RESTART_AT else None)
        if fake.now in PROBES:
            probes[fake.now] = _probe(hass, entry, ids)
    wall_s = os.times().elapsed - started
    await hass.async_block_till_done()

    # -- the wiring: every tick published, every entity moved ---------------- #
    ticks_total = ticks_before_restart + runtime.ticks
    assert len(recorder.ticks) == ticks_total - recorder.unseen, (
        "the coordinator publishes on every tick"
    )
    assert ticks_total >= STEPS, (ticks_total, STEPS)
    assert all(tick.reasons > 0 for tick in recorder.ticks), (
        "reasons on every tick, in observe (INV-44)"
    )
    used_states = {tick.used_kwh for tick in recorder.ticks}
    assert len(used_states) > 1000, "window_used moved through the day"
    first = probes[DAY_START + timedelta(seconds=TICK_S)]
    assert first["price"] not in (None, "unknown", "unavailable"), first
    assert float(first["price"]) > 0.0
    assert first["presence"] == "auto"

    # -- the meter outage: the sensor, the repair, and both clear ------------ #
    # The household is told by the repair; `binary_sensor.<site>_meter_stale`
    # is diagnostic and off by default since WP U.4 (ENT-17), so it has no state.
    assert probes[CHECK_STALE_AT]["meter_stale"] is None, probes[CHECK_STALE_AT]
    assert probes[CHECK_STALE_AT]["meter_stale_issue"] is True
    assert probes[CHECK_FRESH_AT]["meter_stale_issue"] is False

    # -- the store round trip mid-window (INV-14) ---------------------------- #
    before, after = continuity["before"], continuity["after"]
    assert after[0] == before[0], "the window survived the restart"
    assert before[1] - 1e-9 <= after[1] <= before[1] + 0.2, (before, after)
    assert not any(probe["store_reset_issue"] for probe in probes.values())

    # -- the publication timer: tomorrow only after 13:00 CET (INV-6) -------- #
    assert probes[BEFORE_PUBLICATION]["prices_tomorrow"] == "off"
    assert probes[AFTER_PUBLICATION]["prices_tomorrow"] == "on"
    tomorrow_calls = [(at, day, n) for at, day, n in fake.nordpool.calls if day > DAY]
    assert tomorrow_calls, "tomorrow was never fetched"
    assert all(fake.nordpool.published(day, at) for at, day, _n in tomorrow_calls), tomorrow_calls
    assert any(n > 0 for _at, _day, n in tomorrow_calls)

    # -- the bus: every payload valid, the edges the day must have ----------- #
    for event in recorder.events:
        kind = EventKind(event.data["kind"])
        assert event.data["site_id"] == entry.entry_id
        assert events.SCHEMAS[kind](dict(event.data))
    kinds = recorder.kinds()
    assert kinds.count(EventKind.PRICES_RECEIVED.value) >= 2, kinds
    presence = [(row["old"], row["new"]) for row in recorder.of(EventKind.PRESENCE_CHANGED)]
    assert ("home", "away") in presence, presence
    assert ("away", "home") in presence, presence
    assert any(row["active"] for row in recorder.of(EventKind.PEAK_WARNING)), (
        "no peak warning all day"
    )

    # -- the same day through the pure runner ------------------------------- #
    windows = _windows(recorder.ticks)
    starts = [window.start_utc.isoformat() for window in windows]
    assert starts == pure.window_starts, (starts, pure.window_starts)
    assert len(starts) == len(set(starts)), "a window closed twice across the restart"
    assert [window.kwh for window in windows] == pytest.approx(pure.window_kwh, abs=1e-6)
    over_target = sum(window.kwh > TARGET_KW + 1e-9 for window in windows)
    assert over_target == pure.over_target
    period = Period(
        start=datetime(2027, 1, 1, tzinfo=OSLO),
        end=datetime(2027, 2, 1, tzinfo=OSLO),
        key="2027-01",
    )
    assert pure.house is not None
    ha_bill = runtime.build.tariff.bill(period)
    pure_bill = pure.house.tariff.bill(period)
    assert ha_bill.capacity_fee == pure_bill.capacity_fee, (ha_bill, pure_bill)
    assert ha_bill.metric_kw == pytest.approx(pure_bill.metric_kw, abs=1e-6)
    assert fake.calls == [], "nothing is bound in phase 1: no writes reach a device"
    assert pure.writes == {}, "nothing is bound in phase 1: the pure runner wrote nothing either"
    # A coming window's warning names the flat ceiling both sides defend (the
    # live one names the budget's, ε inside it).
    ceilings = {c for tick in recorder.ticks for c in tick.warned_ceilings}
    assert ceilings, "no coming-window warning named a ceiling"
    assert all(c == pytest.approx(TARGET_KW) for c in ceilings), ceilings

    # -- the phase-1 gate (PLAN §3) ------------------------------------------ #
    errors = _projection_errors(recorder.ticks, windows)
    p95 = _p95(errors)
    leads = _leads(recorder.ticks, windows, fake.truth)
    peak_start = max(windows, key=lambda w: w.kwh).start_utc
    summary = {
        "wall_s": round(wall_s, 1),
        "ticks": ticks_total,
        "windows": len(windows),
        "over_target": over_target,
        "peaks": len(leads),
        "fee": f"{ha_bill.capacity_fee.amount} {ha_bill.capacity_fee.currency}",
        "metric_kw": round(ha_bill.metric_kw, 3),
        "projection_p95_kwh": round(p95, 3),
        "projection_samples": len(errors),
        "leads_min": {
            start.astimezone(OSLO).strftime("%H:%M"): (
                None if lead is None else round(lead.total_seconds() / 60.0, 1),
                None if to_start is None else round(to_start.total_seconds() / 60.0, 1),
            )
            for start, (lead, to_start) in leads.items()
        },
        "peak_window": peak_start.astimezone(OSLO).strftime("%H:%M"),
        "events": {kind: kinds.count(kind) for kind in sorted(set(kinds))},
    }
    for key, value in summary.items():
        record_property(key, value)
    _LOGGER.info("e2e day: %s", json.dumps(summary, default=str))
    _LOGGER.info(
        "e2e day, the peak warnings: %s",
        [
            (
                row["at"],
                row.get("warning"),
                row["window_start"],
                row["active"],
                round(float(row["expected_kwh"]), 2),
            )
            for row in recorder.of(EventKind.PEAK_WARNING)
        ],
    )

    assert p95 <= PROJECTION_P95_KWH, summary
    assert leads, "the day has no peak to warn about"
    for start, (lead, _to_start) in leads.items():
        assert lead is not None, (start, summary, _trace(recorder.ticks, start))
        assert lead >= WARNING_LEAD, (start, lead, summary, _trace(recorder.ticks, start))
