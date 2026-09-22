"""D12 §9 17: `plan_status`'s reason as a key, and the plan's next 24 h.

* `reason_key` is a closed set - `ActionReason` - and every producer in
  `core/loads/` names one: the gate's rows, the four kinds, and letting go. Each
  key is driven here from the code that emits it, so a key nothing emits, or an
  emitted key whose sentence needs a number the params do not carry, fails.
* Each key reads in `en` and `nb` (and `strings.json`): a plain label where HA
  shows attribute values (`state_attributes.reason_key.state`, no placeholders -
  hassfest's rule) and a sentence with the params where the dashboard renders it
  (`selector.action_reason.options`, D-0481).
* `sensor.<site>_plan`'s state is the next 24 h of the plan, not all 48 h (B9).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import UnitOfEnergy

from custom_components.powerplan.core.loads import (
    ActionReason,
    Command,
    Hold,
    Mode,
    ReasonParams,
    Role,
    Write,
)
from custom_components.powerplan.core.loads.gate import Transport, decide
from custom_components.powerplan.core.loads.kinds import (
    BatteryMode,
    BatteryModeCfg,
    ControlKind,
    Switch,
    SwitchCfg,
)
from custom_components.powerplan.core.model import (
    Confidence,
    Desired,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
)
from custom_components.powerplan.load_entities import LOAD_SENSORS, PLANNED_REASON
from custom_components.powerplan.sensor import SENSORS, planned_kwh_next_day
from tests.core.loads.conftest import (
    NOW,
    budget,
    floor_load,
    gate_config,
    gate_state,
    grant,
    kind_ctx,
    load_ctx,
    load_state,
    mode_kind,
    modulate_kind,
    reads,
    setpoint_kind,
)

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"
DOCUMENTS = {
    name: json.loads((INTEGRATION / name).read_text(encoding="utf-8"))
    for name in ("strings.json", "translations/en.json", "translations/nb.json")
}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

type Emitted = tuple[ActionReason, ReasonParams]


# --------------------------------------------------------------------------- #
# Driving each producer
# --------------------------------------------------------------------------- #


def _sent(outcome: Command | Hold, current: object = None, *, release: bool = False) -> Emitted:
    """Return the key a kind's outcome reaches the snapshot with: a hold, or the gate's write."""
    if isinstance(outcome, Hold):
        return outcome.reason_key, outcome.reason_params
    decision = decide(
        outcome,
        current=current,  # type: ignore[arg-type]
        mode=Mode.AUTO,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
        release=release,
    )
    return decision.reason_key, decision.reason_params


def _apply(kind: ControlKind, w: float = 0.0, **ctx: Any) -> Emitted:
    context = kind_ctx(**ctx)
    quantised = kind.quantise(w, context)
    return _sent(kind.command(quantised, grant(w, shed=context.shed), context))


def _restore(kind: ControlKind, **ctx: Any) -> Emitted:
    return _sent(kind.restore_command(kind_ctx(**ctx)), release=True)


SELECT = {Role.MODE_SELECT: ["heat", "eco"]}
RELAY = Switch(SwitchCfg(on_at_w=1000.0))
BATTERY = BatteryMode(BatteryModeCfg(charge_w=5000.0, discharge_w=5000.0))
MODES = {Role.BATTERY_MODE: ["general", "eco_charge", "eco_discharge"]}
BUTTON = Switch(SwitchCfg(on_at_w=1000.0, role=Role.START))
WATTS = {"unit": "w", "min_value": 1000.0, "max_value": 10000.0, "step": 100.0}


def _kinds() -> list[tuple[ActionReason, Emitted]]:
    """Every key a kind emits, each from the situation that emits it (D4 §5.3–5.6)."""
    return [
        # Setpoint (§5.4)
        (ActionReason.NO_COMFORT_TARGET, _apply(setpoint_kind())),
        (
            ActionReason.COMFORT_VIOLATED,
            _apply(setpoint_kind(), target=22.0, comfort_violated=True),
        ),
        (ActionReason.PAUSED_SETPOINT, _apply(setpoint_kind(), target=22.0, shed=True)),
        (
            ActionReason.STORE_HEAT,
            _apply(setpoint_kind(charge_setpoint=24.0), target=22.0, desired=Desired.COMFORT),
        ),
        (ActionReason.TARGET, _apply(setpoint_kind(), target=22.0)),
        (ActionReason.TARGET_OFFSET, _apply(setpoint_kind(), target=22.0, setpoint_delta=-1.0)),
        (
            ActionReason.RESTORE_WAIT,
            _apply(
                setpoint_kind(),
                target=22.0,
                held=21.5,
                last_restore_at=NOW - timedelta(seconds=60),
            ),
        ),
        (ActionReason.RESTORE_TARGET, _restore(setpoint_kind(), target=22.0)),
        (ActionReason.NO_COMFORT_TARGET, _restore(setpoint_kind())),
        # Mode (§5.5)
        (ActionReason.NO_OPTION, _apply(mode_kind())),
        (ActionReason.MODE_SAVING, _apply(mode_kind(), reads=reads(options=SELECT), shed=True)),
        (ActionReason.MODE_COMFORT, _apply(mode_kind(), reads=reads(options=SELECT))),
        (ActionReason.RESTORE_MODE, _restore(mode_kind(), reads=reads(options=SELECT))),
        (ActionReason.NO_OPTION, _restore(mode_kind())),
        # Battery mode (§5.9)
        (ActionReason.BATTERY_CHARGE, _apply(BATTERY, 5000.0, reads=reads(options=MODES))),
        (ActionReason.BATTERY_DISCHARGE, _apply(BATTERY, -5000.0, reads=reads(options=MODES))),
        (ActionReason.BATTERY_HOLD, _apply(BATTERY, 0.0, reads=reads(options=MODES))),
        # Switch (§5.6)
        (ActionReason.SWITCH_ON, _apply(RELAY, 2000.0)),
        (ActionReason.PAUSED, _apply(RELAY, 2000.0, shed=True)),
        (ActionReason.NOT_GRANTED, _apply(RELAY)),
        (ActionReason.START_ONLY, _apply(BUTTON)),
        (ActionReason.PROGRAMME_RUNNING, _apply(BUTTON, 2000.0, session_active=True)),
        (ActionReason.RELEASED, _restore(RELAY)),
        # Modulate (§5.3)
        (ActionReason.LIMIT, _apply(modulate_kind(**WATTS), 5000.0)),
        (ActionReason.REDUCE, _apply(modulate_kind(**WATTS), 3000.0, held=5000.0)),
        (ActionReason.STOPPED, _apply(modulate_kind(**WATTS), stop_ok=True)),
        (ActionReason.PARKED, _apply(modulate_kind(**WATTS), park=True)),
        (ActionReason.STAYS_STOPPED, _apply(modulate_kind(**WATTS))),
        (ActionReason.RESUME, _apply(modulate_kind(**WATTS), 5000.0, enabled=False)),
        (ActionReason.ALREADY_HOLDS, _apply(modulate_kind(**WATTS), 5000.0, held=5000.0)),
        (
            ActionReason.DEADBAND,
            _apply(
                modulate_kind(
                    unit="w", min_value=1000.0, max_value=10000.0, step=1.0, suppress_delta=100.0
                ),
                5050.0,
                held=5000.0,
            ),
        ),
        (ActionReason.RELEASED_AT, _restore(modulate_kind(**WATTS))),
    ]


SETPOINT_22 = Command(
    writes=(Write(Role.SETPOINT, 22.0),),
    reason="plan",
    reason_key=ActionReason.TARGET,
    want_on=True,
)


def _row(**options: Any) -> Emitted:
    arguments: dict[str, Any] = {
        "current": 21.0,
        "mode": Mode.AUTO,
        "cfg": gate_config(),
        "state": gate_state(),
        "budget": budget(),
        "now": NOW,
    }
    arguments.update(options)
    command = arguments.pop("command", SETPOINT_22)
    decision = decide(command, **arguments)
    return decision.reason_key, decision.reason_params


def _gate() -> list[tuple[ActionReason, Emitted]]:
    """Every row of D4 §5.10 that holds, fails or observes, and the write itself."""
    wrote = gate_state(last_write_at=NOW - timedelta(seconds=10), last_value=21.0)
    return [
        (ActionReason.OBSERVE_ALREADY_AT, _row(mode=Mode.OBSERVE, current=22.0)),
        (ActionReason.OBSERVE_WOULD_WRITE, _row(mode=Mode.OBSERVE)),
        (ActionReason.DELEGATED, _row(mode=Mode.DELEGATED)),
        (ActionReason.OFF, _row(mode=Mode.OFF)),
        (ActionReason.ALREADY_AT, _row(current=22.0)),
        (
            ActionReason.ALREADY_SENT,
            _row(state=gate_state(last_value=22.0, verify_due=NOW + timedelta(seconds=30))),
        ),
        (
            ActionReason.READBACK_PREDATES,
            _row(
                state=gate_state(
                    last_value=22.0,
                    last_write_at=NOW - timedelta(seconds=70),
                    verify_due=NOW - timedelta(seconds=10),
                ),
                current_at=NOW - timedelta(seconds=80),
            ),
        ),
        (ActionReason.UNAVAILABLE_RETRYING, _row(available=False)),
        (
            ActionReason.UNAVAILABLE_FOR,
            _row(available=False, state=gate_state(transient_since=NOW - timedelta(seconds=30))),
        ),
        (
            ActionReason.SETTLE_WINDOW,
            _row(state=gate_state(last_value=21.0, verify_due=NOW + timedelta(seconds=30))),
        ),
        (ActionReason.INTERVAL, _row(state=wrote)),
        (
            ActionReason.DWELL,
            _row(cfg=gate_config(min_off_s=600.0), state=gate_state(last_off_at=NOW)),
        ),
        (
            ActionReason.BUDGET_EXHAUSTED,
            _row(
                cfg=gate_config(transport=Transport.ZWAVE),
                budget=budget(limits={Transport.ZWAVE: 0}),
            ),
        ),
        (ActionReason.TARGET, _row()),
        (
            ActionReason.SENT,
            _row(command=Command(writes=(Write(Role.SETPOINT, 22.0),), reason="a test")),
        ),
    ]


def _letting_go() -> list[tuple[ActionReason, Emitted]]:
    """`release()` with nothing on record, and with a value to put back (INV-26)."""
    load = floor_load()
    ctx = load_ctx(reads=reads(numbers={Role.SETPOINT: 24.0}))
    _, nothing = load.release(load_state(), ctx)
    _, back = load.release(load_state(prior={str(Role.SETPOINT): 21.0}), ctx)
    return [
        (ActionReason.NOTHING_TO_UNDO, (nothing.reason_key, nothing.reason_params)),
        (ActionReason.BACK_TO_PRIOR, (back.reason_key, back.reason_params)),
    ]


def _emitted() -> list[tuple[ActionReason, Emitted]]:
    return [*_gate(), *_kinds(), *_letting_go()]


# --------------------------------------------------------------------------- #
# §9 17 - the closed set
# --------------------------------------------------------------------------- #


def test_17_every_producer_emits_its_key_and_every_key_is_emitted() -> None:
    """Each situation emits the key it names; together they emit the whole set, nothing else."""
    emitted = _emitted()
    wrong = [(want, got) for want, (got, _params) in emitted if got is not want]
    assert not wrong, wrong
    assert {got for _want, (got, _params) in emitted} == set(ActionReason)


def test_17_every_producer_in_core_names_a_key() -> None:
    """No `Hold`, `Quantised`, `Command`, `ApplyResult`, `Decision` or gate row without a key.

    `Command` has a default (`sent`) so that a test can build one in a line; code
    under `core/` never leans on it.
    """
    missing: list[str] = []
    for path in sorted((INTEGRATION / "core" / "loads").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            keywords = {keyword.arg for keyword in node.keywords}
            if name == "Hold":
                named = len(node.args) >= 3 or "reason_key" in keywords
            elif name == "decided":
                named = len(node.args) >= 3
            elif name in {"Quantised", "Command", "ApplyResult", "Decision"}:
                named = "reason_key" in keywords
            else:
                continue
            if not named:
                missing.append(f"{path.relative_to(INTEGRATION)}:{node.lineno} {name}")
    assert not missing, missing


@pytest.mark.parametrize("document", DOCUMENTS)
def test_17_every_key_is_translated_as_a_label_and_a_sentence(document: str) -> None:
    """Both places carry exactly the set; the label has no placeholder (hassfest)."""
    body = DOCUMENTS[document]
    labels = body["entity"]["sensor"]["plan_status"]["state_attributes"]["reason_key"]["state"]
    sentences = body["selector"]["action_reason"]["options"]
    keys = {key.value for key in ActionReason} | {PLANNED_REASON}
    assert set(labels) == keys
    assert set(sentences) == keys
    assert not [key for key, text in labels.items() if "{" in text]


@pytest.mark.parametrize("document", DOCUMENTS)
def test_17_every_sentence_is_filled_by_the_params_its_producer_sends(document: str) -> None:
    """A placeholder the params do not carry would reach the household as `{value}`."""
    sentences = DOCUMENTS[document]["selector"]["action_reason"]["options"]
    unfilled = sorted(
        (want.value, name)
        for want, (_got, params) in _emitted()
        for name in PLACEHOLDER.findall(sentences[want.value])
        if name not in params
    )
    assert not unfilled, unfilled


def test_17_the_english_reason_is_kept_beside_the_key() -> None:
    """INV-50: nothing a household read is lost - `reason` stays, the key rides beside it."""
    row = next(row for row in LOAD_SENSORS if row.key == "plan_status")
    assert {"reason", "reason_key", "reason_params"} <= row.volatile, "audit F-11"


# --------------------------------------------------------------------------- #
# §9 17 - the plan's next 24 h (B9)
# --------------------------------------------------------------------------- #

START = datetime(2026, 9, 23, 20, 0, tzinfo=UTC)


def _plan(load_id: str, hours: int, kwh: float, start: datetime = START) -> Plan:
    slots = tuple(
        PlanSlot(
            start=start + timedelta(hours=hour),
            end=start + timedelta(hours=hour + 1),
            envelope_w=kwh * 1000.0,
            kwh=kwh,
        )
        for hour in range(hours)
    )
    return Plan(
        load_id=load_id,
        strategy="cheapest_hours",
        mode=PlanMode.PRICE,
        slots=slots,
        built_at=start,
        cost_estimate=Money(Decimal("1.00"), "NOK"),
        confidence=Confidence.KNOWN,
        planned_kwh=kwh * hours,
    )


def test_17_the_plan_state_counts_only_the_next_24_hours() -> None:
    """A 48 h plan of 1 kWh an hour is 24 kWh on the state, from the window `now` is in."""
    plans = [_plan("tank", 48, 1.0), _plan("ev", 48, 0.5)]
    assert planned_kwh_next_day(plans, START + timedelta(minutes=7), 60) == pytest.approx(36.0)
    # Twelve hours on, twelve past hours are gone and twelve of the second day come in.
    later = START + timedelta(hours=12, minutes=40)
    assert planned_kwh_next_day(plans, later, 60) == pytest.approx(36.0)
    # 36 h on, only the plan's last 12 h are left.
    assert planned_kwh_next_day(plans, START + timedelta(hours=36), 60) == pytest.approx(18.0)


def test_17_a_slot_across_the_day_counts_its_share() -> None:
    """Quarter windows over hourly slots: the day runs 20:45 → 20:45, each edge slot prorated."""
    at = START + timedelta(minutes=50)
    # 48 h: a quarter of 20:00's slot, 23 whole slots, three quarters of the next 20:00.
    assert planned_kwh_next_day([_plan("tank", 48, 1.0)], at, 15) == pytest.approx(24.0)
    # 24 h: the plan ends at 20:00 the next day, so only the quarter and the 23 whole.
    assert planned_kwh_next_day([_plan("tank", 24, 1.0)], at, 15) == pytest.approx(23.25)


@dataclass
class _Plans:
    plans: dict[str, Plan]


@dataclass
class _State:
    plans: _Plans


@dataclass
class _Cfg:
    window_min: int


@dataclass
class _Build:
    cfg: _Cfg


@dataclass
class _Runtime:
    state: _State
    build: _Build


@dataclass
class _Snapshot:
    at: datetime
    plans: dict[str, Any]


def test_17_the_site_plan_sensor_reads_the_day_in_kwh() -> None:
    """The row itself: state from the day, unit kWh; `by_load` keeps each whole plan."""
    row = next(row for row in SENSORS if row.key == "plan")
    plan = _plan("tank", 48, 1.0)
    runtime = _Runtime(_State(_Plans({"tank": plan})), _Build(_Cfg(60)))
    snapshot = _Snapshot(at=START + timedelta(minutes=5), plans={})
    assert row.value(snapshot, runtime) == pytest.approx(24.0)  # type: ignore[arg-type]
    assert row.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
