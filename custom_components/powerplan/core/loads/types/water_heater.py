"""`water_heater` - a hot-water cylinder, and the cycle it may not skip (D4 §6.3, §5.12).

Seven questions about a physical thing - litres, kilowatts, people, how it is
steered, when the showers are, whether it has its own anti-legionella programme -
and a review step that reads back what powerplan decided.

Three things make this type different from the other thermal ones.

**The tank is the best store in the house and the worst one to get wrong.** 300 L
between 45 and 75 °C is 10.7 kWh of shiftable energy, which is more than most
Norwegian houses move in an evening. Held at 45 °C to shave a peak it is also a
vessel at the temperature *Legionella pneumophila* likes best, so INV-54 gives it
a deadline the price may not argue with: the cycle is *placed* by price and its
`due_at` is absolute. §5.12's lead window (24 h) is when the planner may choose
the slot; six hours before the deadline the plan stops having a vote.

**Half of them have no thermometer.** The commonest installation in the reference
market is a cylinder on a plug with a mechanical thermostat inside. That is a
`SWITCH` load with a `SensorlessModel` (D4 §5.7): energy in, minus standby loss,
minus the household's draw-off, re-anchored to the dial every time the element is
seen to stop drawing. It is also the reason INV-64 refuses a relay-steered tank
with neither a thermostat nor a sensor - off is a safe state for a tank that
regulates itself, and it is not for one that does not.

**Presence means something different here.** `away` keeps the ready-by deadlines -
a day trip still ends in a shower - `vacation` drops them and lets the tank
coast on its comfort floor, and neither touches the legionella cycle.
"""

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Confidence, Demand, Desired, Grant, Mode, Urgency
from ..base import (
    Load,
    LoadConfig,
    LoadCtx,
    LoadState,
    as_on,
    gate_config,
    recall,
    remember,
)
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.setpoint import Setpoint, SetpointCfg
from ..kinds.switch import Switch, SwitchCfg
from ..questionnaire import (
    AnswerError,
    Answers,
    Bounds,
    Derived,
    Option,
    QCtx,
    Question,
    QuestionKind,
    Questionnaire,
)
from ..stores.thermal import (
    DRAW_L_PER_PERSON_DAY,
    DrawOffProfile,
    SensorlessEstimate,
    SensorlessModel,
    TankStore,
)
from ..targets import PresenceMode
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "DERIVATION_VERSION",
    "ELEMENT_KW",
    "LEGIONELLA_BAND_K",
    "LEGIONELLA_DRIVE_MARGIN_K",
    "LEGIONELLA_HOLD_S",
    "LEGIONELLA_LEAD_H",
    "LEGIONELLA_URGENT_H",
    "READY_BAND_K",
    "TANK_C",
    "TANK_LITRES",
    "Legionella",
    "WaterHeater",
    "draw_off_of",
]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1

#: `LoadState.learned` keys this type owns (`design/DECISIONS.md` D-0200): the
#: sensorless estimate, and how long the water has been in the pasteurisation
#: band on the cycle that is running.
TANK_C: Final = "tank_c"
LEGIONELLA_HOLD_S: Final = "legionella_hold_s"

#: §5.12: the cycle counts as held once the water is within 1 K of its target.
LEGIONELLA_BAND_K: Final = 1.0

#: §5.12: the planner gets a day's notice to place the cycle cheaply…
LEGIONELLA_LEAD_H: Final = 24.0

#: …and loses its vote six hours before the deadline (INV-54).
LEGIONELLA_URGENT_H: Final = 6.0

#: How far **above** the pasteurisation temperature the cycle is driven
#: (`design/DECISIONS.md` D-0203). A mechanical tank thermostat has a 5–8 K
#: differential, so a tank told exactly 65 °C spends half of every reheat below
#: the band and the hold never accumulates.
LEGIONELLA_DRIVE_MARGIN_K: Final = 5.0

#: Tank sizes the flow offers, litres (D4 §6.3 - nameplate sizes).
TANK_LITRES: Final = (100.0, 120.0, 150.0, 200.0, 300.0, 400.0)

#: Element sizes the flow offers, kW (D4 §6.3 - nameplate sizes).
ELEMENT_KW: Final = (1.5, 2.0, 3.0, 4.5, 6.0, 9.0)

#: How much of a tank's charge is worth committing to in one block, minutes
#: (`design/DECISIONS.md` D-0204). D5's `deadline_fill` uses it to refuse the
#: flipping tank: four reversals in 23 minutes on the ancestor controller.
MIN_BLOCK_MIN: Final = 30.0

#: The temperature difference that counts as "there" for a tank, kelvin.
_EPS_K: Final = 0.1

#: A tank within this of its target is *at* its target (§5.12). No tank thermostat
#: resolves finer than a kelvin, and a 0.1 K sensor step is not a demand: told
#: 75 °C at 74.4 °C the element never fires, and a controller that keeps asking
#: re-cuts a 0.4 kWh plan every half hour (`design/DECISIONS.md` D-0256).
READY_BAND_K: Final = 1.0

#: How far ahead the tank looks for its next ready-by (§5.12: two a day).
_DEADLINE_HORIZON: Final = timedelta(hours=36)


def _control_default(ctx: QCtx) -> str:
    """Return how this tank is steered, from what the profile detected (§5.9).

    A plug only when a plug is the *only* thing found. Defaulting the other way
    would put the commonest installation on the kind INV-64 has conditions on,
    and a setpoint tank is one nobody can accidentally leave switched off.
    """
    if {"setpoint", "water_heater", "climate"} & ctx.capabilities:
        return "thermostat"
    if "switch" in ctx.capabilities:
        return "relay"
    return "thermostat"


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="litres",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=f"{litres:.0f}") for litres in TANK_LITRES),
            default="200",
            unit="L",
            help_key="water_heater_litres",
        ),
        Question(
            key="element_kw",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=f"{kw:g}") for kw in ELEMENT_KW),
            default="3",
            unit="kW",
            help_key="water_heater_element",
        ),
        Question(
            key="persons",
            kind=QuestionKind.NUMBER,
            default=2.0,
            min=1.0,
            max=6.0,
            help_key="water_heater_persons",
        ),
        Question(
            key="control",
            kind=QuestionKind.CHOICE,
            options=(Option(value="thermostat"), Option(value="relay")),
            default=_control_default,
            help_key="water_heater_control",
        ),
        Question(
            key="mechanical_thermostat",
            kind=QuestionKind.BOOL,
            # A cylinder without one does not exist in the reference market; the
            # question is asked because INV-64 turns on the answer.
            default=True,
            help_key="water_heater_mechanical",
        ),
        Question(
            key="temp_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="water_heater_temp_entity",
        ),
        Question(
            key="ready_by",
            kind=QuestionKind.TIME,
            default="06:30",
            help_key="water_heater_ready_by",
        ),
        Question(
            key="ready_by_2",
            kind=QuestionKind.TIME,
            default=None,
            help_key="water_heater_ready_by_2",
        ),
        Question(
            key="legionella",
            kind=QuestionKind.CHOICE,
            options=(
                Option(value="built_in"),
                Option(value="powerplan"),
                Option(value="dont_know"),
            ),
            default="powerplan",
            help_key="water_heater_legionella",
        ),
        Question(
            key="comfort_min_c",
            kind=QuestionKind.NUMBER,
            default=45.0,
            unit="°C",
            min=35.0,
            max=70.0,
            advanced=True,
            help_key="water_heater_comfort_min",
        ),
        Question(
            key="ready_temp_c",
            kind=QuestionKind.NUMBER,
            default=75.0,
            unit="°C",
            min=40.0,
            max=85.0,
            advanced=True,
            help_key="water_heater_ready_temp",
        ),
        Question(
            key="max_c",
            kind=QuestionKind.NUMBER,
            default=80.0,
            unit="°C",
            min=45.0,
            max=90.0,
            advanced=True,
            help_key="water_heater_max",
        ),
        Question(
            key="standby_loss_w",
            kind=QuestionKind.NUMBER,
            default=60.0,
            unit="W",
            min=0.0,
            max=500.0,
            advanced=True,
            help_key="water_heater_standby",
        ),
        Question(
            key="legionella_temp_c",
            kind=QuestionKind.NUMBER,
            default=65.0,
            unit="°C",
            min=60.0,
            max=85.0,
            advanced=True,
            help_key="water_heater_legionella_temp",
        ),
        Question(
            key="legionella_hold_min",
            kind=QuestionKind.NUMBER,
            default=60.0,
            unit="min",
            min=10.0,
            max=240.0,
            advanced=True,
            help_key="water_heater_legionella_hold",
        ),
        Question(
            key="legionella_interval_days",
            kind=QuestionKind.NUMBER,
            default=7.0,
            unit="d",
            min=1.0,
            max=30.0,
            advanced=True,
            help_key="water_heater_legionella_interval",
        ),
        Question(
            key="min_on_s",
            kind=QuestionKind.NUMBER,
            default=600.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="water_heater_min_on",
        ),
        Question(
            key="min_off_s",
            kind=QuestionKind.NUMBER,
            default=600.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="water_heater_min_off",
        ),
        Question(
            key="command_interval_s",
            kind=QuestionKind.NUMBER,
            default=120.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="water_heater_interval",
        ),
        Question(
            key="follow_presence",
            kind=QuestionKind.BOOL,
            default=True,
            advanced=True,
            help_key="water_heater_presence",
        ),
        Question(
            key="schedule_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="water_heater_schedule_entity",
        ),
        Question(
            key="arrival_sources",
            kind=QuestionKind.ENTITY,
            default=(),
            advanced=True,
            help_key="water_heater_arrival_sources",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="water_heater_force_max_h",
        ),
    ),
    bounds=(
        Bounds(value="ready_temp_c", low="comfort_min_c", high="max_c"),
        Bounds(value="legionella_temp_c", high="max_c"),
    ),
)


@dataclass(frozen=True, slots=True)
class Legionella:
    """Where the anti-legionella cycle stands (D4 §5.12, INV-54).

    What D7 turns into the `legionella_completed` and `legionella_at_risk` events
    and D8 into a sensor: the type computes it, and nothing here reaches for a
    clock of its own.
    """

    enabled: bool
    due_at: datetime | None
    last_completed: datetime | None
    in_progress: bool
    hold_s: float
    hold_required_s: float
    temp_c: float
    drive_c: float
    at_risk: bool
    #: Inside the lead window or already running: the cycle is asking.
    active: bool
    #: Past `due_at − 6 h`: the plan no longer has a vote (§5.12).
    mandatory: bool

    @property
    def skipped(self) -> bool:
        """Whether the heater runs its own programme, so powerplan runs none."""
        return not self.enabled

    @property
    def hold_remaining_s(self) -> float:
        """Seconds of pasteurisation still owed."""
        return max(0.0, self.hold_required_s - self.hold_s)


@dataclass(frozen=True, slots=True)
class WaterHeater:
    """A hot-water cylinder, on a thermostat or on a plug (D4 §6.3)."""

    key: ClassVar[str] = "water_heater"
    kinds: ClassVar[tuple[str, ...]] = ("setpoint", "switch")
    strategies: ClassVar[tuple[str, ...]] = ("deadline_fill", "heat_capacitor", "surplus", "always")
    default_strategy: ClassVar[str] = "deadline_fill"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, with every number sourced (D4 §6.3)."""
        litres = float(answers.choice("litres"))
        element_kw = float(answers.choice("element_kw"))
        persons = int(answers.number("persons"))
        control = answers.choice("control")
        mechanical = answers.flag("mechanical_thermostat")
        temp_entity = answers.get("temp_entity")
        comfort_min_c = answers.number("comfort_min_c")
        ready_temp_c = answers.number("ready_temp_c")
        max_c = answers.number("max_c")
        legionella_temp_c = answers.number("legionella_temp_c")
        legionella = answers.choice("legionella") != "built_in"

        kind = "setpoint" if control == "thermostat" else "switch"
        has_sensor = control == "thermostat" or temp_entity is not None
        if kind == "switch" and not mechanical and temp_entity is None:
            # INV-64: off is a safe state for a tank that regulates itself, and
            # nothing else. Refused at the boundary, so no load is ever built
            # that needs powerplan to come back to be safe.
            raise AnswerError(
                "control",
                "unsafe_switch",
                "a tank steered by a relay needs a mechanical thermostat or a "
                "temperature sensor: off must be a state the household can live "
                "in indefinitely (INV-64)",
            )

        params: dict[str, Any] = {
            "kind": kind,
            "store": "tank",
            "litres": litres,
            "element_kw": element_kw,
            "nameplate_w": element_kw * 1000.0,
            "persons": persons,
            "control": control,
            "mechanical_thermostat": mechanical,
            "temp_entity": temp_entity,
            "has_temp_sensor": has_sensor,
            "sensorless": not has_sensor,
            "comfort_c": comfort_min_c,
            "comfort_min_c": comfort_min_c,
            "floor_c": comfort_min_c,
            "vacation_c": comfort_min_c,
            "ready_temp_c": ready_temp_c,
            "max_c": max_c,
            "shed_setpoint_c": comfort_min_c,
            # The dial a sensorless estimate re-anchors on. §6.3 does not ask for
            # it, and the ready temperature is the only number in the table that
            # says how hot this tank gets (`design/DECISIONS.md` D-0203).
            "anchor_c": min(ready_temp_c, max_c),
            "ready_by": _hhmm(answers.get("ready_by")),
            "ready_by_2": _hhmm(answers.get("ready_by_2")),
            "legionella": legionella,
            "legionella_source": answers.choice("legionella"),
            "legionella_temp_c": legionella_temp_c,
            "legionella_drive_c": min(
                max_c, max(ready_temp_c, legionella_temp_c + LEGIONELLA_DRIVE_MARGIN_K)
            ),
            "legionella_hold_min": answers.number("legionella_hold_min"),
            "legionella_interval_days": answers.number("legionella_interval_days"),
            "legionella_lead_h": LEGIONELLA_LEAD_H,
            "legionella_urgent_h": LEGIONELLA_URGENT_H,
            "standby_loss_w": answers.number("standby_loss_w"),
            "draw_l_per_person_day": DRAW_L_PER_PERSON_DAY,
            "min_on_s": answers.number("min_on_s"),
            "min_off_s": answers.number("min_off_s"),
            "command_interval_s": answers.number("command_interval_s"),
            "follow_presence": answers.flag("follow_presence"),
            "schedule_entity": answers.get("schedule_entity"),
            "arrival_sources": [str(entity) for entity in answers.get("arrival_sources") or ()],
            "force_max_h": answers.number("force_max_h"),
            "substitutable": False,
            "phases": 1,
        }
        # What the household is actually being offered, in kilowatt-hours: the
        # review step says "10.7 kWh of shiftable hot water" and not "300 L".
        params["shiftable_kwh"] = round(
            self._store_from(params).capacity_kwh_per_unit() * (ready_temp_c - comfort_min_c), 2
        )
        return Derived(
            params=params,
            strategy=self.default_strategy,
            strategy_params={"min_block_min": max(MIN_BLOCK_MIN, params["min_on_s"] / 60.0)},
            # Above the floor loops' 30 and below the heat pumps' 50: a tank
            # coasts for hours where a room coasts for minutes, and a cold shower
            # is noticed where a slab drifting half a degree is not
            # (`design/DECISIONS.md` D-0203).
            priority=40,
            group=None,
            explanation_key="water_heater_review",
            explanation_params={
                "litres": litres,
                "element_kw": element_kw,
                "persons": persons,
                "kind": kind,
                "comfort_min_c": comfort_min_c,
                "ready_temp_c": ready_temp_c,
                "ready_by": params["ready_by"],
                "legionella": legionella,
                "legionella_temp_c": legionella_temp_c,
                "legionella_interval_days": params["legionella_interval_days"],
                "sensorless": params["sensorless"],
                "shiftable_kwh": params["shiftable_kwh"],
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: the kind, the tank and the gate (D4 §5.1)."""
        if cfg.target is None:
            raise ValueError(
                f"{cfg.load_id}: water_heater needs a target profile — a comfort "
                "target comes from configuration, never from the device (INV-27)"
            )
        kind = self._kind(cfg)
        return Load(
            config=cfg,
            kind=kind,
            device_type=self,
            gate=gate_config(kind, cfg),
            store=store if store is not None else self._store(cfg),
        )

    def _kind(self, cfg: LoadConfig) -> ControlKind:
        """`SETPOINT` on a thermostat, `SWITCH` on a plug (§5.4, §5.6, INV-64)."""
        params = cfg.params
        comfort_min_c = float(params.get("comfort_min_c", 45.0))
        interval = float(params.get("command_interval_s", 120.0))
        min_on_s = float(params.get("min_on_s", 600.0))
        min_off_s = float(params.get("min_off_s", 600.0))
        if params.get("kind") == "switch":
            return Switch(
                SwitchCfg(
                    on_at_w=float(params.get("nameplate_w", 3000.0)),
                    min_on_s=min_on_s,
                    min_off_s=min_off_s,
                    min_interval_s=interval,
                    # A relay is the blunt lever: §5.10 makes it urgent from
                    # stage 3, where a thermostat is urgent from stage 2.
                    urgent_from_stage=3,
                )
            )
        return Setpoint(
            SetpointCfg(
                shed_setpoint=comfort_min_c,
                # The tank's charge value is not one number: it is the comfort
                # minimum, the ready temperature or the legionella drive,
                # decided per tick by the type and handed over as `target`
                # (`design/DECISIONS.md` D-0203).
                charge_setpoint=None,
                device_min=comfort_min_c,
                device_max=float(params.get("max_c", 80.0)),
                min_on_s=min_on_s,
                min_off_s=min_off_s,
                min_interval_s=interval,
                urgent_from_stage=2,
            )
        )

    def _store(self, cfg: LoadConfig) -> TankStore:
        """Return the cylinder, with a sensorless model where there is no sensor."""
        return self._store_from(cfg.params)

    def _store_from(self, params: Mapping[str, Any]) -> TankStore:
        """Return the `TankStore` these parameters describe (§4.3, §5.7)."""
        litres = float(params.get("litres", 200.0))
        comfort_min_c = float(params.get("comfort_min_c", 45.0))
        max_c = float(params.get("max_c", 80.0))
        standby_loss_w = float(params.get("standby_loss_w", 60.0))
        sensorless: SensorlessModel | None = None
        if params.get("sensorless"):
            sensorless = SensorlessModel(
                litres=litres,
                standby_loss_w=standby_loss_w,
                anchor_c=float(params.get("anchor_c", 75.0)),
                floor_c=comfort_min_c,
                draw=draw_off_of(params),
            )
        return TankStore(
            litres=litres,
            standby_loss_w=standby_loss_w,
            max_c=max_c,
            min_c=comfort_min_c,
            sensorless=sensorless,
        )

    # -------------------------------------------------------------------- tick #

    def level(self, load: Load, state: LoadState, ctx: LoadCtx) -> float | None:
        """Return the water temperature - measured, or estimated (§5.7).

        Never both: a tank with a thermometer has no estimate to fall back on,
        and a tank without one has no reading to prefer.
        """
        if not load.config.params.get("sensorless"):
            return ctx.reads.value(Role.TEMP)
        remembered = recall(state, TANK_C)
        return None if remembered is None else remembered.value

    def comfort(self, load: Load, state: LoadState, ctx: LoadCtx) -> ComfortState:
        """Where the tank stands against its configured minimum (INV-27, INV-55)."""
        profile = load.config.target
        assert profile is not None  # build() refuses a tank without one
        target = profile.target(ctx.now, ctx.presence)
        level = self.level(load, state, ctx)
        return ComfortState(
            current=level,
            target=target,
            floor=profile.floor,
            ceiling=profile.ceiling,
            violated=level is not None and level < profile.floor,
            deficit=0.0 if level is None else target - level,
            direction=profile.direction,
        )

    # -------------------------------------------------------------- legionella #

    def legionella(self, load: Load, state: LoadState, ctx: LoadCtx) -> Legionella:
        """Return where the anti-legionella cycle stands (§5.12, INV-54)."""
        params = load.config.params
        enabled = bool(params.get("legionella", True))
        temp_c = float(params.get("legionella_temp_c", 65.0))
        drive_c = float(params.get("legionella_drive_c", temp_c + LEGIONELLA_DRIVE_MARGIN_K))
        hold_required_s = float(params.get("legionella_hold_min", 60.0)) * 60.0
        if not enabled:
            return Legionella(
                enabled=False,
                due_at=None,
                last_completed=state.legionella_last_completed,
                in_progress=False,
                hold_s=0.0,
                hold_required_s=hold_required_s,
                temp_c=temp_c,
                drive_c=drive_c,
                at_risk=False,
                active=False,
                mandatory=False,
            )

        last = state.legionella_last_completed
        due_at = (
            None
            if last is None
            else last + timedelta(days=float(params.get("legionella_interval_days", 7.0)))
        )
        lead = timedelta(hours=float(params.get("legionella_lead_h", LEGIONELLA_LEAD_H)))
        urgent = timedelta(hours=float(params.get("legionella_urgent_h", LEGIONELLA_URGENT_H)))
        in_progress = state.legionella_in_progress_since is not None
        held = recall(state, LEGIONELLA_HOLD_S)
        hold_s = 0.0 if held is None else held.value
        active = in_progress or (due_at is not None and ctx.now >= due_at - lead)
        mandatory = in_progress and due_at is not None and ctx.now >= due_at - urgent
        return Legionella(
            enabled=True,
            due_at=due_at,
            last_completed=last,
            in_progress=in_progress,
            hold_s=hold_s,
            hold_required_s=hold_required_s,
            temp_c=temp_c,
            drive_c=drive_c,
            at_risk=self._at_risk(
                load,
                state,
                ctx,
                due_at=due_at,
                drive_c=drive_c,
                hold_left_s=hold_required_s - hold_s,
            )
            if in_progress
            else False,
            active=active,
            mandatory=mandatory,
        )

    def _at_risk(
        self,
        load: Load,
        state: LoadState,
        ctx: LoadCtx,
        *,
        due_at: datetime | None,
        drive_c: float,
        hold_left_s: float,
    ) -> bool:
        """Whether the cycle can still finish in time (§8 `legionella_at_risk`).

        An element too small or a week of sheds is a warning, never a silence.
        """
        if due_at is None:
            return False
        element_kw = float(load.config.params.get("nameplate_w", 3000.0)) / 1000.0
        if element_kw <= 0.0:
            return True
        level = self.level(load, state, ctx)
        store = load.store
        required = (
            None if store is None else store.required_kwh(level, drive_c, None, ctx.store_ctx())
        )
        needed_h = (0.0 if required is None else required / element_kw) + hold_left_s / 3600.0
        return needed_h > (due_at - ctx.now).total_seconds() / 3600.0

    # ------------------------------------------------------------- what to aim #

    def deadlines(
        self, load: Load, state: LoadState, ctx: LoadCtx
    ) -> tuple[tuple[datetime, float], ...]:
        """Every moment the tank must be at a temperature, and which (§5.12).

        The ready-by times belong to the household, so `vacation` drops them and
        `away` keeps them; the legionella deadline belongs to the bacteria, so
        nothing drops it (INV-54, INV-55).
        """
        params = load.config.params
        found: list[tuple[datetime, float]] = []
        if ctx.presence is not PresenceMode.VACATION:
            ready_temp = float(params.get("ready_temp_c", 75.0))
            zone = self._zone(ctx)
            for key in ("ready_by", "ready_by_2"):
                at = _next_local(ctx.now, zone, params.get(key))
                if at is not None and at < ctx.now + _DEADLINE_HORIZON:
                    found.append((at, ready_temp))
        cycle = self.legionella(load, state, ctx)
        if cycle.active and cycle.due_at is not None:
            found.append((cycle.due_at, cycle.drive_c))
        return tuple(sorted(found))

    def aim(self, load: Load, state: LoadState, ctx: LoadCtx) -> tuple[float, str]:
        """Return the temperature to write **now**, and why (§5.4's tank row).

        The tank's charge setpoint is not one number: the comfort minimum most of
        the day, the ready temperature when the plan says charge, the legionella
        drive while the cycle is actually being run.

        A cycle that is merely *due* does not raise the aim by itself - it is
        placed by price (§5.12), and a shower that dips the tank under its floor
        inside the lead window must not drag the whole cycle forward. That is the
        flipping tank's four reversals in 23 minutes in a different costume.
        """
        params = load.config.params
        cycle = self.legionella(load, state, ctx)
        charging = ctx.desired is Desired.COMFORT or state.mode is Mode.FORCE
        if cycle.in_progress and (cycle.mandatory or charging):
            why = "the deadline is absolute" if cycle.mandatory else "placed by price"
            return cycle.drive_c, f"legionella cycle to {cycle.drive_c:.0f} °C: {why}"
        comfort = self.comfort(load, state, ctx)
        if charging:
            ready = min(float(params.get("ready_temp_c", 75.0)), comfort.ceiling or 80.0)
            why = "forced" if state.mode is Mode.FORCE else "the plan says charge"
            return ready, f"{why}: {ready:.0f} °C"
        return comfort.target, f"comfort minimum {comfort.target:.0f} °C"

    def requirement(
        self, load: Load, state: LoadState, ctx: LoadCtx
    ) -> tuple[float, datetime | None]:
        """Return what the planner must deliver, and by when (§5.12).

        The nearest deadline and its temperature - that is what `deadline_fill`
        paces against. With no deadline at all the tank only owes its comfort
        minimum.
        """
        deadlines = self.deadlines(load, state, ctx)
        if not deadlines:
            return self.comfort(load, state, ctx).target, None
        at, target = deadlines[0]
        return target, at

    # ------------------------------------------------------------------ latches #

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Advance the sensorless estimate, then the legionella cycle (§5.7, §5.12)."""
        state = self._estimate(load, state, ctx)
        return self._legionella_latch(load, state, ctx)

    def _estimate(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Move the sensorless estimate on by one tick (§5.7)."""
        store = load.store
        model = None if not isinstance(store, TankStore) else store.sensorless
        if model is None:
            return state
        remembered = recall(state, TANK_C)
        previous = (
            None
            if remembered is None
            else SensorlessEstimate(
                temp_c=remembered.value, at=remembered.at, confidence=Confidence.ESTIMATED
            )
        )
        estimate = model.advance(
            previous,
            at=ctx.now,
            measured_w=ctx.reads.value(Role.POWER),
            powered=self._powered(load, ctx),
            zone=self._zone(ctx),
        )
        return remember(state, ctx.now, **{TANK_C: estimate.temp_c})

    def _legionella_latch(  # noqa: PLR0911 - start, hold, lose, complete, skip: five outcomes
        self, load: Load, state: LoadState, ctx: LoadCtx
    ) -> LoadState:
        """Start, hold and complete the cycle (§5.12, INV-54)."""
        params = load.config.params
        if not bool(params.get("legionella", True)):
            if state.legionella_in_progress_since is None:
                return state
            return remember(
                replace(state, legionella_in_progress_since=None),
                ctx.now,
                **{LEGIONELLA_HOLD_S: None},
            )

        if state.legionella_last_completed is None:
            # Adoption: before powerplan the tank sat at its own dial, well above
            # 60 °C, so "now" is the honest anchor - and it is written down once,
            # so the deadline cannot slide away tick by tick (D-0203).
            state = replace(state, legionella_last_completed=ctx.now)

        cycle = self.legionella(load, state, ctx)
        if not cycle.in_progress:
            if cycle.active:
                return replace(state, legionella_in_progress_since=ctx.now)
            return state

        level = self.level(load, state, ctx)
        in_band = level is not None and level >= cycle.temp_c - LEGIONELLA_BAND_K
        if not in_band:
            return remember(state, ctx.now, **{LEGIONELLA_HOLD_S: None})

        held = recall(state, LEGIONELLA_HOLD_S)
        accumulated = 0.0 if held is None else held.value + (ctx.now - held.at).total_seconds()
        if accumulated >= cycle.hold_required_s:
            return remember(
                replace(
                    state,
                    legionella_last_completed=ctx.now,
                    legionella_in_progress_since=None,
                ),
                ctx.now,
                **{LEGIONELLA_HOLD_S: None},
            )
        return remember(state, ctx.now, **{LEGIONELLA_HOLD_S: accumulated})

    # ------------------------------------------------------------------ demand #

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the tank wants, how badly, and by when (§5.12)."""
        comfort = self.comfort(load, state, ctx)
        cycle = self.legionella(load, state, ctx)
        target, deadline = self.requirement(load, state, ctx)
        level = comfort.current
        forced = state.mode is Mode.FORCE
        store = load.store
        wants = level is None or level < target - READY_BAND_K
        required = (
            None
            if store is None
            else 0.0
            if not wants and level is not None
            else store.required_kwh(level, target, deadline, ctx.store_ctx())
        )

        if comfort.violated:
            urgency = Urgency.COMFORT_VIOLATION
            reason = f"below the {comfort.floor:.0f} °C floor"
        elif cycle.active:
            urgency = Urgency.LEGIONELLA
            reason = (
                f"legionella cycle to {cycle.drive_c:.0f} °C"
                f"{' (the plan has no vote)' if cycle.mandatory else ''}"
            )
        elif not wants:
            urgency = Urgency.NONE
            reason = f"at {target:.0f} °C"
        elif deadline is not None:
            urgency = Urgency.DEADLINE
            reason = f"{target:.0f} °C by {deadline:%H:%M}"
        else:
            urgency = Urgency.NORMAL
            reason = f"{target:.0f} °C wanted"

        return Demand(
            wants=wants or forced,
            required_kwh=required,
            deadline=deadline,
            min_w=0.0,
            max_w=load.config.nameplate_w,
            urgency=urgency,
            comfort=comfort,
            price_sensitive=(not comfort.violated and not forced and not cycle.mandatory),
            reason="forced" if forced and urgency is Urgency.NONE else reason,
        )

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the kind needs, with the tank's own target resolved (§5.4)."""
        comfort = self.comfort(load, state, ctx)
        cycle = self.legionella(load, state, ctx)
        target, _ = self.aim(load, state, ctx)
        return KindCtx(
            now=ctx.now,
            reads=ctx.reads,
            electrical=ctx.electrical,
            phases=load.config.phases,
            mode=mode,
            stage=0 if grant is None else grant.stage,
            shed=False if grant is None else grant.shed,
            stop_ok=False if grant is None else grant.stop_ok,
            blunt=False if grant is None else grant.blunt,
            target=target,
            floor=comfort.floor,
            ceiling=comfort.ceiling,
            setpoint_delta=ctx.setpoint_delta,
            # A mandatory cycle takes the plan's vote away: INV-54's deadline is
            # absolute, and a stale `SHED` would otherwise hold the tank at its
            # floor through the last six hours before it.
            desired=None if cycle.mandatory else ctx.desired,
            comfort_violated=comfort.violated,
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
            on_at_w=load.config.nameplate_w,
        )

    # --------------------------------------------------------------- internals #

    def _powered(self, load: Load, ctx: LoadCtx) -> bool:
        """Whether the element has mains **because we allow it** (§5.7).

        A plug we switched off, or one nothing can read, is not an anchor: a tank
        drawing nothing for a reason of ours says nothing about the water in it,
        and blindness never opens a gate (INV-15, INV-17).
        """
        if load.config.params.get("kind") != "switch":
            return True
        return as_on(ctx.reads.current_of(Role.SWITCH))

    def _zone(self, ctx: LoadCtx) -> tzinfo:
        """Return the zone the household's clock statements are made in (HLD §7.1)."""
        zone = ctx.zone if ctx.zone is not None else ctx.now.tzinfo
        assert zone is not None  # every datetime in the core is tz-aware
        return zone


def draw_off_of(params: Mapping[str, Any]) -> DrawOffProfile:
    """Return the household's hot water as materialised (D4 §5.7, INV-66).

    One reading for the two that need it: the sensorless model's integrator here,
    and D11's tank shadow, which reheats what the same household draws.
    """
    return DrawOffProfile(
        persons=int(params.get("persons", 2)),
        litres_per_person_day=float(params.get("draw_l_per_person_day", DRAW_L_PER_PERSON_DAY)),
    )


def _hhmm(value: Any) -> str | None:
    """Return a time answer as the "HH:MM" a subentry holds (D4 §4.6, §7)."""
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


def _next_local(now: datetime, zone: tzinfo, raw: Any) -> datetime | None:
    """Return the next local occurrence of `raw` ("HH:MM"), or `None`."""
    if raw is None:
        return None
    if isinstance(raw, time):
        moment = raw
    else:
        hour, minute = (int(part) for part in str(raw).split(":", 1))
        moment = time(hour=hour, minute=minute)
    local = now.astimezone(zone)
    for ahead in range(2):
        candidate = (local + timedelta(days=ahead)).replace(
            hour=moment.hour, minute=moment.minute, second=0, microsecond=0
        )
        if candidate > local:
            return candidate.astimezone(now.tzinfo)
    return None


TYPE = register(WaterHeater())
