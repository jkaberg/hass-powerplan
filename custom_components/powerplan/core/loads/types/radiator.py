"""`radiator` - a panel heater, on a plug or on its own thermostat (D4 §6.5).

The simplest thermal load in the house and the one with the least to give: a
panel heater has no store worth the name, so what it can offer is a *short* pause
in an expensive half-hour, which is exactly what `best_save` asks of it - shed
when the slot is more than 10 % above the day's mean, never off for more than two
hours, never on for less than thirty minutes.

Two ways in, both of them in §6.5: a plug in front of it (`SWITCH`, with the
heater's own dial doing the regulating - INV-64's safe state, because a dial is
what the household lives on when powerplan is gone) or a thermostat we can set
(`SETPOINT`). The room table is §6.1's, which is measured comfort in the
reference house; a radiator's target is air where a floor loop's is floor, and the
pairs are the same because they were measured on the same rooms.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, gate_config
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.setpoint import Setpoint, SetpointCfg
from ..kinds.switch import Switch, SwitchCfg
from ..questionnaire import (
    Answers,
    Bounds,
    Derived,
    Option,
    QCtx,
    Question,
    QuestionKind,
    Questionnaire,
)
from ..stores.thermal import RoomStore
from .base import register
from .floor_heating import ROOM_DEFAULTS, room_from_area

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "DERIVATION_VERSION",
    "HEATER_TYPES",
    "HeaterType",
    "Radiator",
]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class HeaterType:
    """What a kind of heater implies (D4 §6.5): nameplate and the room it sits in.

    `area_m2` is §6.5's "`RoomStore` mass hint" made concrete - the floor area a
    heater of this size is bought for, which is what the room's heat capacity is
    computed from. A measured power always wins over the nameplate.
    """

    watts: float
    area_m2: float


#: D4 §6.5's nameplate defaults. The areas are the rooms those sizes are sold
#: for: a towel rail heats a bathroom, a convector an open living room
#: (`design/DECISIONS.md` D-0206).
HEATER_TYPES: Mapping[str, HeaterType] = {
    "oil_filled": HeaterType(watts=1000.0, area_m2=15.0),
    "panel": HeaterType(watts=800.0, area_m2=12.0),
    "convector": HeaterType(watts=1200.0, area_m2=20.0),
    "towel_rail": HeaterType(watts=500.0, area_m2=6.0),
}

#: `best_save`'s three numbers, straight out of §6.5.
SAVE_THRESHOLD_PCT: Final = 10.0
SAVE_MAX_OFF_MIN: Final = 120.0
SAVE_MIN_ON_MIN: Final = 30.0

#: How far above comfort a room may be banked (`design/DECISIONS.md` D-0206).
#: §6.5 gives no maximum; two kelvin is what a panel heater can put into a room
#: before the household opens a window, and INV-56 needs *a* number.
BANK_ABOVE_COMFORT_K: Final = 2.0
#: A panel heater's band: its own dial and the room's lag between plug cycles.
#: What the type steers against sits half of it above the floor (D-0265).
PLUG_BAND_K: Final = 1.0

#: How far ahead a radiator looks for its next step-up; D5 plans the rest (§5.8).
_DEADLINE_HORIZON: Final = timedelta(hours=24)


def _control_default(ctx: QCtx) -> str:
    """Return a thermostat where the profile detected one, else a plug (§5.9)."""
    if {"setpoint", "climate"} & ctx.capabilities:
        return "thermostat"
    return "plug"


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="heater_type",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in HEATER_TYPES),
            # The commonest thing on a Norwegian wall, and the middle of the
            # nameplate range.
            default="panel",
            help_key="radiator_heater_type",
        ),
        Question(
            key="room",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in ROOM_DEFAULTS),
            default=room_from_area,
            help_key="radiator_room",
        ),
        Question(
            key="control",
            kind=QuestionKind.CHOICE,
            options=(Option(value="plug"), Option(value="thermostat")),
            default=_control_default,
            help_key="radiator_control",
        ),
        Question(
            key="power_w",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="W",
            min=100.0,
            max=5000.0,
            help_key="radiator_power",
        ),
        Question(
            key="power_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="radiator_power_entity",
        ),
        Question(
            key="area_m2",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="m²",
            min=2.0,
            max=200.0,
            help_key="radiator_area",
        ),
        Question(
            key="comfort_c",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="°C",
            min=5.0,
            max=30.0,
            help_key="radiator_comfort",
        ),
        Question(
            key="min_c",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="°C",
            min=5.0,
            max=25.0,
            help_key="radiator_min",
        ),
        Question(
            key="min_on_s",
            kind=QuestionKind.NUMBER,
            # §6.5's "min on 30 min", as a clock the gate enforces (row 7).
            default=SAVE_MIN_ON_MIN * 60.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="radiator_min_on",
        ),
        Question(
            key="min_off_s",
            kind=QuestionKind.NUMBER,
            default=SAVE_MIN_ON_MIN * 60.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="radiator_min_off",
        ),
        Question(
            key="command_interval_s",
            kind=QuestionKind.NUMBER,
            default=300.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="radiator_interval",
        ),
        Question(
            key="follow_presence",
            kind=QuestionKind.BOOL,
            default=True,
            advanced=True,
            help_key="radiator_presence",
        ),
        Question(
            key="schedule_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="radiator_schedule_entity",
        ),
        Question(
            key="arrival_sources",
            kind=QuestionKind.ENTITY,
            default=(),
            advanced=True,
            help_key="radiator_arrival_sources",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="radiator_force_max_h",
        ),
    ),
    bounds=(Bounds(value="comfort_c", low="min_c"),),
)


@dataclass(frozen=True, slots=True)
class Radiator:
    """A panel heater, an oil-filled radiator, a convector, a towel rail."""

    key: ClassVar[str] = "radiator"
    kinds: ClassVar[tuple[str, ...]] = ("switch", "setpoint")
    strategies: ClassVar[tuple[str, ...]] = ("best_save", "heat_capacitor", "always")
    default_strategy: ClassVar[str] = "best_save"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, with every number sourced (D4 §6.5)."""
        heater = HEATER_TYPES[answers.choice("heater_type")]
        room = answers.choice("room")
        defaults = ROOM_DEFAULTS[room]
        control = answers.choice("control")
        area_m2 = _or(answers.get("area_m2"), heater.area_m2)
        nameplate_w = _or(answers.get("power_w"), heater.watts)
        comfort_c = _or(answers.get("comfort_c"), defaults.comfort_c)
        floor_c = _or(answers.get("min_c"), defaults.floor_c)

        params: dict[str, Any] = {
            "kind": "setpoint" if control == "thermostat" else "switch",
            "store": "room",
            "heater_type": answers.choice("heater_type"),
            "room": room,
            "control": control,
            "nameplate_w": nameplate_w,
            "power_entity": answers.get("power_entity"),
            "area_m2": area_m2,
            "volume_m3": round(area_m2 * 2.5, 1),
            "comfort_c": comfort_c,
            "floor_c": floor_c,
            "vacation_c": floor_c + 1.0,
            "max_c": comfort_c + BANK_ABOVE_COMFORT_K,
            "shed_setpoint_c": floor_c,
            # A panel heater's room has no fitted loss coefficient until D10
            # fits one; the loss term is skipped rather than invented (§5.7).
            "heat_loss_w_per_k": None,
            "min_on_s": answers.number("min_on_s"),
            "min_off_s": answers.number("min_off_s"),
            "command_interval_s": answers.number("command_interval_s"),
            "follow_presence": answers.flag("follow_presence"),
            "schedule_entity": answers.get("schedule_entity"),
            "arrival_sources": [str(entity) for entity in answers.get("arrival_sources") or ()],
            "force_max_h": answers.number("force_max_h"),
            "substitutable": room != "bathroom",
            "phases": 1,
        }
        return Derived(
            params=params,
            strategy=self.default_strategy,
            strategy_params={
                "threshold_pct": SAVE_THRESHOLD_PCT,
                "max_off_min": SAVE_MAX_OFF_MIN,
                "min_on_min": SAVE_MIN_ON_MIN,
            },
            priority=defaults.priority,
            group="radiators",
            explanation_key="radiator_review",
            explanation_params={
                "heater_type": answers.choice("heater_type"),
                "room": room,
                "control": control,
                "nameplate_w": nameplate_w,
                "comfort_c": comfort_c,
                "floor_c": floor_c,
                "threshold_pct": SAVE_THRESHOLD_PCT,
                "max_off_min": SAVE_MAX_OFF_MIN,
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: a relay or a setpoint, and the room (§5.1)."""
        if cfg.target is None:
            raise ValueError(
                f"{cfg.load_id}: radiator needs a target profile — a comfort target "
                "comes from configuration, never from the device (INV-27)"
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
        """`SWITCH` behind a plug, `SETPOINT` on a thermostat (§6.5)."""
        params = cfg.params
        min_on_s = float(params.get("min_on_s", 1800.0))
        min_off_s = float(params.get("min_off_s", 1800.0))
        interval = float(params.get("command_interval_s", 300.0))
        if params.get("kind") == "switch":
            return Switch(
                SwitchCfg(
                    on_at_w=float(params.get("nameplate_w", 800.0)),
                    min_on_s=min_on_s,
                    min_off_s=min_off_s,
                    min_interval_s=interval,
                    # §5.10: a relay is urgent from stage 3.
                    urgent_from_stage=3,
                )
            )
        floor_c = float(params.get("floor_c", 17.0))
        return Setpoint(
            SetpointCfg(
                shed_setpoint=float(params.get("shed_setpoint_c", floor_c)) + _half_band(params),
                device_min=floor_c,
                device_max=float(params.get("max_c", 24.0)),
                min_on_s=min_on_s,
                min_off_s=min_off_s,
                min_interval_s=interval,
                # A room with a panel heater falls in minutes, not hours: it is
                # the fast lever, urgent from stage 2 like a tank's thermostat.
                urgent_from_stage=2,
            )
        )

    def _store(self, cfg: LoadConfig) -> RoomStore:
        """Return the room this heater is in (§5.7)."""
        params = cfg.params
        loss = params.get("heat_loss_w_per_k")
        return RoomStore.from_volume(
            volume_m3=float(params.get("volume_m3", 30.0)),
            max_c=float(params.get("max_c", 24.0)),
            min_c=float(params.get("floor_c", 17.0)),
            heat_loss_w_per_k=None if loss is None else float(loss),
        )

    # -------------------------------------------------------------------- tick #

    def level(self, load: Load, ctx: LoadCtx) -> float | None:
        """Return the room temperature - air, wherever it is read from."""
        return ctx.reads.value(Role.TEMP)

    def comfort(self, load: Load, ctx: LoadCtx) -> ComfortState:
        """Where the room stands against its configured target (INV-27, INV-55)."""
        profile = load.config.target
        assert profile is not None  # build() refuses a radiator without one
        # The target the heater is steered towards never sits closer to the floor
        # than half a band: a plug that closes only below the floor lets the room
        # fall through it (D-0265). The floor itself is untouched (INV-55).
        target = max(
            profile.target(ctx.now, ctx.presence), profile.floor + _half_band(load.config.params)
        )
        level = self.level(load, ctx)
        return ComfortState(
            current=level,
            target=target,
            floor=profile.floor,
            ceiling=profile.ceiling,
            violated=level is not None and level < profile.floor,
            deficit=0.0 if level is None else target - level,
            direction=profile.direction,
        )

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the heater wants, measured against comfort (§6.5)."""
        comfort = self.comfort(load, ctx)
        profile = load.config.target
        assert profile is not None
        deadlines = profile.deadlines(
            ctx.now, ctx.now + _DEADLINE_HORIZON, ctx.presence, ctx.calendar
        )
        deadline = deadlines[0][0] if deadlines else None
        wants = comfort.current is None or comfort.deficit > 0.0
        forced = state.mode is Mode.FORCE
        required = (
            None
            if load.store is None
            else load.store.required_kwh(comfort.current, comfort.target, deadline, ctx.store_ctx())
        )
        if comfort.violated:
            urgency = Urgency.COMFORT_VIOLATION
        elif not wants:
            urgency = Urgency.NONE
        elif deadline is not None:
            urgency = Urgency.DEADLINE
        else:
            urgency = Urgency.NORMAL
        # A measured power beats the nameplate the moment one is bound (§6.5).
        measured = ctx.reads.value(Role.POWER)
        return Demand(
            wants=wants or forced,
            required_kwh=required,
            deadline=deadline,
            min_w=0.0,
            max_w=load.config.nameplate_w
            if measured is None
            else max(measured, load.config.nameplate_w),
            urgency=urgency,
            comfort=comfort,
            price_sensitive=not comfort.violated and not forced,
            reason=_reason(comfort, wants=wants, forced=forced),
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Return the state unchanged: a panel heater has nothing to latch."""
        return state

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the kind needs, with the comfort numbers resolved (§5.4, §5.6)."""
        comfort = self.comfort(load, ctx)
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
            target=comfort.target,
            floor=comfort.floor + _half_band(load.config.params),
            ceiling=comfort.ceiling,
            setpoint_delta=ctx.setpoint_delta,
            desired=ctx.desired,
            comfort_violated=comfort.violated,
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
            on_at_w=load.config.nameplate_w,
        )


def _or(value: Any, fallback: float) -> float:
    """Return the answered number, or the derived default when it was left blank."""
    return fallback if value is None else float(value)


def _half_band(params: Mapping[str, Any]) -> float:
    """Return half the heater's band (`swing_k`, else `PLUG_BAND_K`): what a room dips by."""
    return float(params.get("swing_k", PLUG_BAND_K)) / 2.0


def _reason(comfort: ComfortState, *, wants: bool, forced: bool) -> str:
    """One line for the snapshot and the log."""
    if comfort.violated:
        return f"below the {comfort.floor:.1f} °C floor"
    if forced:
        return "forced"
    if not wants:
        return f"at {comfort.target:.1f} °C"
    if comfort.current is None:
        return "room temperature unknown"
    return f"{comfort.deficit:.1f} K under {comfort.target:.1f} °C"


TYPE = register(Radiator())
