"""`floor_heating` - an electric floor loop, or a water-borne one (D4 §6.1, §5.5).

Six questions, all with defaults, and a review step that reads back what was
decided: "a heavy slab under wood in a bathroom - powerplan charges it at night,
lets it coast through the morning, never above 27 °C, never substituted."

Every number in the derivation tables below has a source. The comfort and floor
temperatures are the reference house's measured configuration; the covering
maxima are EN 1264's 29 °C surface limit for occupied zones and the 27 °C that
wood and laminate manufacturers commonly specify; the W/m² figures are the
manufacturer ranges for cable (60–100) and mat (100–150) systems; the slab
physics is ρ 2200, cp 0.9.

The loop is steered by its operation-mode select where the thermostat has one -
a mode toggle is atomic and is not an NVM write, unlike a setpoint (D4 §5.5) -
and by its setpoint otherwise. Which of the two is decided by what the profile
detected, which arrives in `QCtx.capabilities`.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, gate_config
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.mode import ModeCfg, ModeKind
from ..kinds.setpoint import Setpoint, SetpointCfg
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
from ..stores.thermal import RoomStore, SlabStore
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "COVERING_MAX_C",
    "DERIVATION_VERSION",
    "HEATING_TYPES",
    "ROOM_DEFAULTS",
    "FloorHeating",
    "HeatingType",
    "RoomDefaults",
    "room_from_area",
]

#: Bumped whenever a table below changes. A materialised load keeps the version
#: it was derived with, and nothing recomputes behind the user's back (INV-66).
DERIVATION_VERSION: Final = 1

#: How far ahead a loop looks for its next step-up; D5 plans the rest (§5.8).
_DEADLINE_HORIZON: Final = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class RoomDefaults:
    """What a room implies (D4 §6.1): comfort, floor and shed priority."""

    comfort_c: float
    floor_c: float
    priority: int


#: Measured comfort in the reference house; bathrooms are warm by intent and are
#: never substituted by a heat pump. `other` takes the hall's neutral pair
#: (`design/DECISIONS.md` D-0066).
ROOM_DEFAULTS: Mapping[str, RoomDefaults] = {
    "bathroom": RoomDefaults(comfort_c=24.0, floor_c=21.0, priority=32),
    "living": RoomDefaults(comfort_c=22.0, floor_c=19.0, priority=30),
    "kitchen": RoomDefaults(comfort_c=22.0, floor_c=19.0, priority=30),
    "hall": RoomDefaults(comfort_c=21.0, floor_c=19.0, priority=30),
    "bedroom": RoomDefaults(comfort_c=19.0, floor_c=17.0, priority=30),
    "other": RoomDefaults(comfort_c=21.0, floor_c=19.0, priority=30),
}

#: Maximum floor temperature by covering (D4 §6.1). EN 1264 limits the surface
#: to 29 °C in occupied zones; wood and laminate are commonly 27 °C.
COVERING_MAX_C: Mapping[str, float] = {
    "tile": 30.0,
    "wood": 27.0,
    "laminate": 27.0,
    "vinyl": 28.0,
    "carpet": 28.0,
}


@dataclass(frozen=True, slots=True)
class HeatingType:
    """What the heating construction implies (D4 §6.1)."""

    store: str
    strategy: str
    w_per_m2: float | None
    screed_mm: float


#: "Don't know" is a cable in screed: it is what most Norwegian bathrooms have,
#: and the slab model is the conservative one - it plans a fill it can coast on.
HEATING_TYPES: Mapping[str, HeatingType] = {
    "cable_in_screed": HeatingType("slab", "heat_capacitor", 80.0, 40.0),
    "foil_or_mat": HeatingType("room", "best_save", 120.0, 0.0),
    "water_borne": HeatingType("hydronic", "heat_capacitor", None, 40.0),
    "dont_know": HeatingType("slab", "heat_capacitor", 80.0, 40.0),
}

#: The HA area name, lowercased, matched against the room options.
_AREA_TOKENS: Mapping[str, str] = {
    "bath": "bathroom",
    "bad": "bathroom",
    "wc": "bathroom",
    "living": "living",
    "stue": "living",
    "kitchen": "kitchen",
    "kjøkken": "kitchen",
    "hall": "hall",
    "gang": "hall",
    "entr": "hall",
    "bed": "bedroom",
    "sov": "bedroom",
}


def room_from_area(ctx: QCtx) -> str:
    """Prefill the room from the HA area where its name says so (HLD §7.9)."""
    area = (ctx.area or "").strip().lower()
    for token, room in _AREA_TOKENS.items():
        if token in area:
            return room
    return "other"


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="room",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in ROOM_DEFAULTS),
            default=room_from_area,
            help_key="floor_heating_room",
        ),
        Question(
            key="covering",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in COVERING_MAX_C),
            # The conservative cap: a tile floor under 27 °C is limited
            # needlessly, a wood floor under 30 °C is damaged.
            default="wood",
            help_key="floor_heating_covering",
        ),
        Question(
            key="heating_type",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in HEATING_TYPES),
            default="dont_know",
            help_key="floor_heating_type",
        ),
        Question(
            key="area_m2",
            kind=QuestionKind.NUMBER,
            default=10.0,
            unit="m²",
            min=1.0,
            max=400.0,
            help_key="floor_heating_area",
        ),
        Question(
            key="sensor",
            kind=QuestionKind.CHOICE,
            options=(Option(value="floor"), Option(value="air"), Option(value="both")),
            default="floor",
            help_key="floor_heating_sensor",
        ),
        Question(
            key="comfort_c",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="°C",
            min=5.0,
            max=35.0,
            help_key="floor_heating_comfort",
        ),
        Question(
            key="min_c",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="°C",
            min=5.0,
            max=30.0,
            help_key="floor_heating_min",
        ),
        Question(
            key="max_c",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="°C",
            min=10.0,
            max=40.0,
            help_key="floor_heating_max",
        ),
        Question(
            key="screed_depth_mm",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="mm",
            min=0.0,
            max=200.0,
            advanced=True,
            help_key="floor_heating_screed",
        ),
        Question(
            key="loss_coeff_w_per_k",
            kind=QuestionKind.NUMBER,
            default=None,
            derived_default=True,
            unit="W/K",
            min=0.0,
            max=2000.0,
            advanced=True,
            help_key="floor_heating_loss",
        ),
        Question(
            key="swing_k",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="K",
            min=0.2,
            max=5.0,
            advanced=True,
            help_key="floor_heating_swing",
        ),
        Question(
            key="min_on_s",
            kind=QuestionKind.NUMBER,
            default=900.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="floor_heating_min_on",
        ),
        Question(
            key="min_off_s",
            kind=QuestionKind.NUMBER,
            default=900.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="floor_heating_min_off",
        ),
        Question(
            key="command_interval_s",
            kind=QuestionKind.NUMBER,
            default=600.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="floor_heating_interval",
        ),
        Question(
            key="follow_presence",
            kind=QuestionKind.BOOL,
            default=True,
            advanced=True,
            help_key="floor_heating_presence",
        ),
        Question(
            key="schedule_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="floor_heating_schedule_entity",
        ),
        Question(
            key="arrival_sources",
            kind=QuestionKind.ENTITY,
            default=(),
            advanced=True,
            help_key="floor_heating_arrival_sources",
        ),
    ),
    bounds=(Bounds(value="comfort_c", low="min_c", high="max_c"),),
)


@dataclass(frozen=True, slots=True)
class FloorHeating:
    """An electric or water-borne floor loop (D4 §6.1)."""

    key: ClassVar[str] = "floor_heating"
    kinds: ClassVar[tuple[str, ...]] = ("mode", "setpoint")
    strategies: ClassVar[tuple[str, ...]] = ("heat_capacitor", "best_save", "always")
    default_strategy: ClassVar[str] = "heat_capacitor"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, with every number sourced (D4 §6.1)."""
        room = answers.choice("room")
        defaults = ROOM_DEFAULTS[room]
        covering = answers.choice("covering")
        heating = HEATING_TYPES[answers.choice("heating_type")]
        area_m2 = answers.number("area_m2")

        comfort_c = _or(answers.get("comfort_c"), defaults.comfort_c)
        floor_c = _or(answers.get("min_c"), defaults.floor_c)
        max_c = _or(answers.get("max_c"), COVERING_MAX_C[covering])
        screed_mm = _or(answers.get("screed_depth_mm"), heating.screed_mm)
        swing_k = _or(answers.get("swing_k"), 1.0 if room == "bathroom" else 1.5)
        hydronic = heating.store == "hydronic"
        nameplate_w = 0.0 if heating.w_per_m2 is None else heating.w_per_m2 * area_m2
        kind = "mode" if "mode_select" in ctx.capabilities else "setpoint"

        params: dict[str, Any] = {
            "kind": kind,
            "store": heating.store,
            "room": room,
            "covering": covering,
            "heating_type": answers.choice("heating_type"),
            "sensor": answers.choice("sensor"),
            "comfort_c": comfort_c,
            "floor_c": floor_c,
            "max_c": max_c,
            "shed_setpoint_c": floor_c,
            "eco_setpoint_c": round(comfort_c - 2.0, 2),
            "floor_min_limit_c": floor_c,
            "area_m2": area_m2,
            "screed_mm": screed_mm,
            "w_per_m2": heating.w_per_m2,
            "nameplate_w": nameplate_w,
            "kwh_per_k": round(_slab(area_m2, screed_mm).capacity_kwh_per_unit(), 4)
            if heating.store == "slab"
            else round(
                RoomStore.from_volume(
                    volume_m3=area_m2 * 2.5, max_c=max_c, min_c=floor_c
                ).capacity_kwh_per_unit(),
                4,
            ),
            "loss_coeff_w_per_k": answers.get("loss_coeff_w_per_k"),
            "swing_k": swing_k,
            "min_on_s": answers.number("min_on_s"),
            "min_off_s": answers.number("min_off_s"),
            "command_interval_s": answers.number("command_interval_s"),
            "substitutable": room != "bathroom",
            "follow_presence": answers.flag("follow_presence"),
            "schedule_entity": answers.get("schedule_entity"),
            "arrival_sources": [str(entity) for entity in answers.get("arrival_sources") or ()],
            "hydronic": hydronic,
            "phases": 1,
        }
        return Derived(
            params=params,
            strategy=heating.strategy,
            strategy_params={"swing_k": swing_k},
            priority=defaults.priority,
            group="floor_heating",
            explanation_key="floor_heating_review",
            explanation_params={
                "room": room,
                "covering": covering,
                "store": heating.store,
                "comfort_c": comfort_c,
                "max_c": max_c,
                "nameplate_w": nameplate_w,
                "strategy": heating.strategy,
                "substitutable": room != "bathroom",
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: kind, store and gate configuration (D4 §5.1)."""
        if cfg.target is None:
            raise ValueError(
                f"{cfg.load_id}: floor_heating needs a target profile — a comfort "
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
        """`MODE` where the thermostat has a select, `SETPOINT` otherwise (§5.5)."""
        params = cfg.params
        interval = float(params.get("command_interval_s", 600.0))
        if params.get("kind") == "mode":
            return ModeKind(
                ModeCfg(
                    comfort_option=str(params.get("comfort_option", "heat")),
                    shed_option=str(params.get("shed_option", "eco")),
                    min_interval_s=interval,
                )
            )
        floor_c = float(params.get("floor_c", 19.0))
        # A thermostat holds its setpoint ± half its swing, so the lowest setpoint
        # that keeps the floor is half a swing above it (`design/DECISIONS.md` D-0259).
        return Setpoint(
            SetpointCfg(
                shed_setpoint=float(params.get("shed_setpoint_c", floor_c)) + _half_swing(params),
                device_min=floor_c,
                device_max=float(params.get("max_c", 27.0)),
                min_on_s=float(params.get("min_on_s", 900.0)),
                min_off_s=float(params.get("min_off_s", 900.0)),
                min_interval_s=interval,
                # A slab is the slow lever: its shed is urgent from stage 3,
                # where a thermostat on a tank is urgent from stage 2 (§5.10).
                urgent_from_stage=3,
            )
        )

    def _store(self, cfg: LoadConfig) -> StoreModel | None:
        """Return the store model the heating type implies (§6.1)."""
        params = cfg.params
        kind = str(params.get("store", "slab"))
        max_c = float(params.get("max_c", 27.0))
        floor_c = float(params.get("floor_c", 19.0))
        area_m2 = float(params.get("area_m2", 10.0))
        loss = params.get("loss_coeff_w_per_k")
        if kind == "room":
            return RoomStore.from_volume(
                volume_m3=area_m2 * 2.5,
                max_c=max_c,
                min_c=floor_c,
                heat_loss_w_per_k=None if loss is None else float(loss),
            )
        return SlabStore(
            area_m2=area_m2,
            screed_mm=float(params.get("screed_mm", 40.0)),
            loss_coeff_w_per_k=None if loss is None else float(loss),
            max_c=max_c,
            min_c=floor_c,
        )

    # -------------------------------------------------------------------- tick #

    def level(self, load: Load, ctx: LoadCtx) -> float | None:
        """Return the temperature comfort refers to, per the sensor answer (§6.1)."""
        sensor = str(load.config.params.get("sensor", "floor"))
        if sensor == "air":
            return ctx.reads.value(Role.TEMP)
        floor = ctx.reads.value(Role.TEMP_FLOOR)
        return floor if floor is not None else ctx.reads.value(Role.TEMP)

    def comfort(self, load: Load, ctx: LoadCtx) -> ComfortState:
        """Where the loop stands against its configured target (INV-27, INV-55)."""
        profile = load.config.target
        assert profile is not None  # build() refuses a loop without one
        target = profile.target(ctx.now, ctx.presence)
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
        """Return what the loop wants, measured against comfort and not against a shed."""
        comfort = self.comfort(load, ctx)
        profile = load.config.target
        assert profile is not None
        deadlines = profile.deadlines(
            ctx.now, ctx.now + _DEADLINE_HORIZON, ctx.presence, ctx.calendar
        )
        deadline = deadlines[0][0] if deadlines else None
        wants = comfort.current is None or comfort.deficit > 0.0
        required = (
            None
            if load.store is None
            else load.store.required_kwh(comfort.current, comfort.target, deadline, ctx.store_ctx())
        )
        forced = state.mode is Mode.FORCE
        if comfort.violated:
            urgency = Urgency.COMFORT_VIOLATION
        elif not wants:
            urgency = Urgency.NONE
        elif deadline is not None:
            urgency = Urgency.DEADLINE
        else:
            urgency = Urgency.NORMAL
        return Demand(
            wants=wants or forced,
            required_kwh=required,
            deadline=deadline,
            min_w=0.0,
            max_w=load.config.nameplate_w,
            urgency=urgency,
            comfort=comfort,
            price_sensitive=not comfort.violated and not forced,
            reason=_reason(comfort, wants, forced),
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Return the state unchanged: a floor loop has no latch, the slab is its own memory."""
        return state

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the kind needs, with the comfort numbers already resolved."""
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
            floor=comfort.floor + _half_swing(load.config.params),
            ceiling=comfort.ceiling,
            setpoint_delta=ctx.setpoint_delta,
            desired=ctx.desired,
            comfort_violated=comfort.violated,
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
            on_at_w=load.config.nameplate_w,
        )


def _half_swing(params: Mapping[str, Any]) -> float:
    """Return half the thermostat's swing (§6.1 `swing_k`): what a setpoint dips by."""
    return float(params.get("swing_k", 0.0)) / 2.0


def _or(value: Any, fallback: float) -> float:
    """Return the answered number, or the derived default when it was left blank."""
    return fallback if value is None else float(value)


def _slab(area_m2: float, screed_mm: float) -> SlabStore:
    """Return a slab store for the kWh/K figure only."""
    return SlabStore(
        area_m2=area_m2, screed_mm=screed_mm, loss_coeff_w_per_k=None, max_c=27.0, min_c=5.0
    )


def _reason(comfort: ComfortState, wants: bool, forced: bool) -> str:
    """One line for the snapshot and the log."""
    if comfort.violated:
        return f"below the {comfort.floor:.1f} °C floor"
    if forced:
        return "forced"
    if not wants:
        return f"at {comfort.target:.1f} °C"
    if comfort.current is None:
        return "temperature unknown"
    return f"{comfort.deficit:.1f} K under {comfort.target:.1f} °C"


TYPE = register(FloorHeating())
