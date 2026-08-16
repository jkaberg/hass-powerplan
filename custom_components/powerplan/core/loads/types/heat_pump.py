"""`heat_pump` - generic by design, and bounded by INV-29 (D4 §6.4, §5.14).

There is no product profile for a heat pump and there is not going to be one. A
heat pump is *a climate entity plus five numbers the household can read off a
label*: type, rated electrical power, a COP curve, the heated area, the age of the
building. Everything else powerplan computes forward - expected draw is heat
demand ÷ COP(T_out) capped at rated, the reservation is what the thing is
measurably drawing plus a margin, the forecast ceiling is the rated power. The
brand is only where the climate entity came from (D4 §11).

INV-29 is the whole of the rest of this module, and every clause of it is a defect
this design exists to prevent:

* **the band is rejected, not clipped.** A `band_k` of 2.5 fails the questionnaire
  and fails the kind. A configuration that silently became something else is how
  a house ends up 3 K off what its owner typed.
* **restore, never adopt.** The first write after every start is the configured comfort
  value, and no upward move happens within one dwell of it. Eight restarts took a
  setpoint from 22 to 26 °C on the ancestor controller because start-up read the device
  and added its own offset.
* **never shed during a defrost.** A unit that has reversed its cycle to melt its
  own coil draws its full rated power and heats *nothing*; the indoor outlet air
  goes below room temperature. Shedding there buys no comfort back, and forty-five
  minutes later it happens again. §5.14's signature - power up while the outlet
  falls - is what `latch()` watches for.
* **the mains switch is never actuated.** `kinds` contains `setpoint` and nothing
  else, so there is no relay for this type to reach for, and the entities the
  household lists as `never_switch` travel into the subentry as data so the
  executor can refuse them too.
* **no urgent path.** §5.10: a compressor is not hurried. A shed waits for the
  900 s command interval like every other write on this device.
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, gate_config, recall, remember
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.setpoint import BAND_MAX_K, Setpoint, SetpointCfg
from ..questionnaire import Answers, Derived, Option, QCtx, Question, QuestionKind, Questionnaire
from ..stores.cop import DEFAULT_COP_CURVES, CopCurve
from ..stores.thermal import RoomStore
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "BUILDING_U_VALUE",
    "DEFROST_MAX_S",
    "DEFROST_POWER_FRACTION",
    "DERIVATION_VERSION",
    "OUTLET_C",
    "POWER_W",
    "RESERVE_MARGIN_FRACTION",
    "HeatPump",
    "curve_of",
]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1

#: `LoadState.learned` keys this type owns: the previous outlet and power sample
#: the defrost detector compares against (`design/DECISIONS.md` D-0200).
OUTLET_C: Final = "outlet_c"
POWER_W: Final = "power_w"

#: Envelope heat loss by building period, W/m²K (D4 §6.4). Typical Norwegian
#: envelope values: pre-1980 uninsulated-to-lightly, TEK97 era, TEK07, TEK10+.
BUILDING_U_VALUE: Final[Mapping[str, float]] = {
    "before_1980": 1.6,
    "1980_2000": 1.0,
    "2000_2010": 0.7,
    "after_2010": 0.5,
}

#: Room height the heated area is turned into a volume with (D4 §6.4).
ROOM_HEIGHT_M: Final = 2.5

#: A defrost draws hard. Under half the rated power the unit is idling or
#: modulating, and neither is a defrost (§5.14).
DEFROST_POWER_FRACTION: Final = 0.5

#: How long a defrost latch may live. A cycle is five minutes; a latch that stuck
#: would suppress every shed for ever, which is a worse failure than shedding a
#: defrosting unit once (`design/DECISIONS.md` D-0205).
DEFROST_MAX_S: Final = 600.0

#: The outlet being this far above the room again means the unit is heating.
DEFROST_CLEAR_K: Final = 1.0

#: How far the outlet must fall between two samples to count as falling.
DEFROST_OUTLET_FALL_K: Final = 0.2

#: What an inverter may step up to before the next tick sees it: a fifth of what
#: it is drawing now (`design/DECISIONS.md` D-0205). The reservation is
#: `measured × 1.2`, never the nameplate - an inverter at 23 W does not reserve
#: 3 kW (§5.14).
RESERVE_MARGIN_FRACTION: Final = 0.2

#: Stages run 1–4, so this is "never" - §5.10: a heat pump has no urgent path.
_NEVER_URGENT: Final = 5

#: The ladder stage at which the pump coasts a kelvin under target (§5.4).
_COAST_STAGE: Final = 3

#: How far the comfort floor sits under the comfort target, kelvin
#: (`design/DECISIONS.md` D-0205). One kelvin under the `away` setback, so an empty
#: house rests above its own floor instead of on it.
FLOOR_BELOW_COMFORT_K: Final = 4.0

#: How far ahead the pump looks for its next step-up; D5 plans the rest (§5.8).
_DEADLINE_HORIZON: Final = timedelta(hours=24)


def _rated_default(ctx: QCtx) -> float:
    """Return the rated power the device reported, else D4 §6.4's 3 kW."""
    return ctx.readable.get("rated_kw", 3.0)


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="hp_type",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=key) for key in DEFAULT_COP_CURVES),
            default="a2a",
            help_key="heat_pump_type",
        ),
        Question(
            key="area_m2",
            kind=QuestionKind.NUMBER,
            # The open-plan part of the reference house, which is what one
            # air-to-air unit serves (D9 §5.9).
            default=60.0,
            unit="m²",
            min=5.0,
            max=600.0,
            help_key="heat_pump_area",
        ),
        Question(
            key="building",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=key) for key in BUILDING_U_VALUE),
            default="2000_2010",
            help_key="heat_pump_building",
        ),
        Question(
            key="rated_kw",
            kind=QuestionKind.NUMBER,
            default=_rated_default,
            unit="kW",
            min=0.3,
            max=30.0,
            help_key="heat_pump_rated",
        ),
        Question(
            key="comfort_c",
            kind=QuestionKind.NUMBER,
            default=21.0,
            unit="°C",
            min=5.0,
            max=30.0,
            help_key="heat_pump_comfort",
        ),
        Question(
            key="cop_curve",
            kind=QuestionKind.CURVE,
            default=None,
            derived_default=True,
            advanced=True,
            help_key="heat_pump_cop_curve",
        ),
        Question(
            key="band_k",
            kind=QuestionKind.NUMBER,
            default=1.0,
            unit="K",
            min=0.2,
            # Rejected, not clipped (INV-29): the questionnaire refuses 2.5 K and
            # `SetpointCfg` refuses it again if anything gets past here.
            max=BAND_MAX_K,
            advanced=True,
            help_key="heat_pump_band",
        ),
        Question(
            key="command_interval_s",
            kind=QuestionKind.NUMBER,
            # §6.4's "min setpoint interval 900 s", under the name the gate and
            # every other type already use for it.
            default=900.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="heat_pump_interval",
        ),
        Question(
            key="dwell_s",
            kind=QuestionKind.NUMBER,
            default=1800.0,
            unit="s",
            min=0.0,
            max=14400.0,
            advanced=True,
            help_key="heat_pump_dwell",
        ),
        Question(
            key="preheat",
            kind=QuestionKind.BOOL,
            default=False,
            advanced=True,
            help_key="heat_pump_preheat",
        ),
        Question(
            key="preheat_max_outdoor_c",
            kind=QuestionKind.NUMBER,
            default=5.0,
            unit="°C",
            min=-20.0,
            max=20.0,
            advanced=True,
            help_key="heat_pump_preheat_max_outdoor",
            asked_if="preheat",
        ),
        Question(
            key="never_switch",
            kind=QuestionKind.ENTITY,
            default=(),
            advanced=True,
            help_key="heat_pump_never_switch",
        ),
        Question(
            key="outdoor_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="heat_pump_outdoor_entity",
        ),
        Question(
            key="outlet_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="heat_pump_outlet_entity",
        ),
        Question(
            key="follow_presence",
            kind=QuestionKind.BOOL,
            default=True,
            advanced=True,
            help_key="heat_pump_presence",
        ),
        Question(
            key="schedule_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="heat_pump_schedule_entity",
        ),
        Question(
            key="arrival_sources",
            kind=QuestionKind.ENTITY,
            default=(),
            advanced=True,
            help_key="heat_pump_arrival_sources",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="heat_pump_force_max_h",
        ),
    )
)


def curve_of(load: Load) -> CopCurve:
    """Return the load's COP curve, rebuilt from what was materialised (INV-66)."""
    stored = load.config.params.get("cop_curve") or {}
    if not stored:
        return CopCurve.flat(1.0)
    return CopCurve.from_mapping({float(key): float(value) for key, value in stored.items()})


@dataclass(frozen=True, slots=True)
class HeatPump:
    """A climate entity, a rated power and a curve (D4 §6.4, §5.14)."""

    key: ClassVar[str] = "heat_pump"
    #: One kind, and it is not a relay: INV-29's mains switch has nothing to be
    #: actuated *by*. `sg_ready` joins it in v1.x (D4 §10).
    kinds: ClassVar[tuple[str, ...]] = ("setpoint",)
    strategies: ClassVar[tuple[str, ...]] = ("heat_capacitor", "best_save", "always")
    default_strategy: ClassVar[str] = "heat_capacitor"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, with every number sourced (D4 §6.4)."""
        hp_type = answers.choice("hp_type")
        area_m2 = answers.number("area_m2")
        building = answers.choice("building")
        rated_kw = answers.number("rated_kw")
        comfort_c = answers.number("comfort_c")
        band_k = answers.number("band_k")
        u_value = BUILDING_U_VALUE[building]
        curve = answers.get("cop_curve") or dict(DEFAULT_COP_CURVES[hp_type].points)
        floor_c = comfort_c - FLOOR_BELOW_COMFORT_K

        params: dict[str, Any] = {
            "kind": "setpoint",
            "store": "room",
            "hp_type": hp_type,
            "area_m2": area_m2,
            "volume_m3": round(area_m2 * ROOM_HEIGHT_M, 1),
            "building": building,
            "u_envelope_w_per_m2k": u_value,
            "heat_loss_w_per_k": round(u_value * area_m2, 2),
            "rated_kw": rated_kw,
            "nameplate_w": rated_kw * 1000.0,
            "cop_curve": {f"{float(point)}": float(cop) for point, cop in sorted(curve.items())},
            "comfort_c": comfort_c,
            "floor_c": floor_c,
            "vacation_c": floor_c + 1.0,
            # INV-29 bounds the setpoint to `comfort ± band`, so the store's
            # maximum is the top of that band and nothing may bank past it
            # (INV-56).
            "max_c": comfort_c + band_k,
            "shed_setpoint_c": floor_c,
            "band_k": band_k,
            "device_min_c": ctx.readable.get("min_temp"),
            "device_max_c": ctx.readable.get("max_temp"),
            "command_interval_s": answers.number("command_interval_s"),
            "dwell_s": answers.number("dwell_s"),
            "preheat": answers.flag("preheat"),
            "preheat_max_outdoor_c": answers.number("preheat_max_outdoor_c"),
            "never_switch": [str(entity) for entity in answers.get("never_switch") or ()],
            "outdoor_entity": answers.get("outdoor_entity"),
            "outlet_entity": answers.get("outlet_entity"),
            "can_cool": "cool" in ctx.capabilities,
            "follow_presence": answers.flag("follow_presence"),
            "schedule_entity": answers.get("schedule_entity"),
            "arrival_sources": [str(entity) for entity in answers.get("arrival_sources") or ()],
            "force_max_h": answers.number("force_max_h"),
            "substitutable": True,
            "phases": 1,
        }
        return Derived(
            params=params,
            strategy=self.default_strategy,
            # Preheat is the strategy's gate, not the kind's: §5.4 says a +Δ
            # "arrives already gated by D5", so the two knobs §6.4 asks for
            # travel to D5 and the type only clamps to the band
            # (`design/DECISIONS.md` D-0205).
            strategy_params={
                "band_k": band_k,
                "preheat": params["preheat"],
                "preheat_max_outdoor_c": params["preheat_max_outdoor_c"],
                "cop_curve": params["cop_curve"],
            },
            # The reference house's heat pumps, above the floor loops' 30 and the
            # tank's 40: a room served by a heat pump coasts for minutes where a
            # slab coasts for hours, and it is the whole house's heating.
            priority=50,
            group=None,
            explanation_key="heat_pump_review",
            explanation_params={
                "hp_type": hp_type,
                "area_m2": area_m2,
                "building": building,
                "rated_kw": rated_kw,
                "comfort_c": comfort_c,
                "band_k": band_k,
                "heat_loss_w_per_k": params["heat_loss_w_per_k"],
                "cop_at_minus_seven": round(curve_at(params["cop_curve"], -7.0), 2),
                "preheat": params["preheat"],
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: one setpoint kind and a room (D4 §5.1)."""
        if cfg.target is None:
            raise ValueError(
                f"{cfg.load_id}: heat_pump needs a target profile — a comfort "
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
        """Return the `SETPOINT` kind, banded and unhurried (§5.4, §5.10)."""
        params = cfg.params
        band_k = float(params.get("band_k", 1.0))
        return Setpoint(
            SetpointCfg(
                # The hard comfort floor, not the band's bottom: the band moves
                # with the target under `away`, and the type hands the band
                # bottom over as `KindCtx.floor` every tick.
                shed_setpoint=float(params.get("floor_c", 17.0)),
                charge_setpoint=None,
                band_up=band_k,
                band_down=band_k,
                device_min=params.get("device_min_c"),
                device_max=params.get("device_max_c"),
                min_on_s=float(params.get("dwell_s", 1800.0)),
                min_off_s=float(params.get("dwell_s", 1800.0)),
                # §5.10: 0.25 °C, 300 s and 120 s for a heat pump, and its own
                # 900 s interval and 1 800 s dwell bind first.
                tolerance=0.25,
                min_interval_s=float(params.get("command_interval_s", 900.0)),
                verify_after_s=120.0,
                restore_dwell_s=float(params.get("dwell_s", 1800.0)),
                urgent_from_stage=_NEVER_URGENT,
            )
        )

    def _store(self, cfg: LoadConfig) -> RoomStore:
        """Return the room the pump heats (§5.7)."""
        params = cfg.params
        loss = params.get("heat_loss_w_per_k")
        return RoomStore.from_volume(
            volume_m3=float(params.get("volume_m3", 150.0)),
            max_c=float(params.get("max_c", 22.0)),
            min_c=float(params.get("floor_c", 17.0)),
            heat_loss_w_per_k=None if loss is None else float(loss),
        )

    # -------------------------------------------------------------------- tick #

    def level(self, load: Load, ctx: LoadCtx) -> float | None:
        """Return the room temperature the climate entity reports."""
        return ctx.reads.value(Role.TEMP)

    def outdoor_c(self, ctx: LoadCtx) -> float | None:
        """Return the outdoor temperature: the bound sensor, else the site's (D10)."""
        bound = ctx.reads.value(Role.OUTDOOR_TEMP)
        return bound if bound is not None else ctx.outdoor_c

    def comfort(self, load: Load, ctx: LoadCtx) -> ComfortState:
        """Where the room stands against its configured target (INV-27, INV-55)."""
        profile = load.config.target
        assert profile is not None  # build() refuses a pump without one
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

    # ------------------------------------------------------------------ defrost #

    def defrosting(self, load: Load, state: LoadState, ctx: LoadCtx) -> bool:
        """Whether the unit is melting its coil right now (§5.14, INV-29).

        Bounded by `DEFROST_MAX_S`: the answer "yes, for ever" would suppress
        every shed this load could ever make.
        """
        since = state.defrost_since
        if since is None:
            return False
        return (ctx.now - since).total_seconds() <= DEFROST_MAX_S

    def _signature(self, load: Load, state: LoadState, ctx: LoadCtx) -> bool:
        """Say whether this tick looks like a defrost (§5.14).

        Two witnesses. The first is a *state*: drawing at least half the rated
        power while the indoor outlet air is no warmer than the room - a heating
        unit's outlet runs ten to twenty kelvin above it. The second is §5.14's
        literal derivative, power up while the outlet falls, which catches a unit
        whose outlet sensor sits somewhere kinder.

        Blindness never invents a defrost: a missing reading is not a signature
        (INV-15, INV-17).
        """
        power = ctx.reads.value(Role.POWER)
        outlet = ctx.reads.value(Role.OUTLET_TEMP)
        room = self.level(load, ctx)
        if power is None or outlet is None or room is None:
            return False
        rated_w = float(load.config.params.get("nameplate_w", 3000.0))
        if power < DEFROST_POWER_FRACTION * rated_w:
            return False
        if outlet <= room:
            return True
        previous_power = recall(state, POWER_W)
        previous_outlet = recall(state, OUTLET_C)
        if previous_power is None or previous_outlet is None:
            return False
        return power > previous_power.value and outlet < previous_outlet.value - (
            DEFROST_OUTLET_FALL_K
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Set and clear the defrost latch, and keep this tick's samples (§5.14)."""
        outlet = ctx.reads.value(Role.OUTLET_TEMP)
        room = self.level(load, ctx)
        if state.defrost_since is None:
            if self._signature(load, state, ctx):
                state = replace(state, defrost_since=ctx.now)
        else:
            recovered = outlet is not None and room is not None and outlet > room + DEFROST_CLEAR_K
            elapsed = (ctx.now - state.defrost_since).total_seconds()
            if recovered or elapsed > DEFROST_MAX_S:
                state = replace(state, defrost_since=None)
        return remember(
            state,
            ctx.now,
            **{OUTLET_C: outlet, POWER_W: ctx.reads.value(Role.POWER)},
        )

    # ------------------------------------------------------------------- draw #

    def heat_demand_w(self, load: Load, ctx: LoadCtx) -> float | None:
        """Steady-state heat the room needs at this outdoor temperature (§5.14)."""
        outdoor = self.outdoor_c(ctx)
        loss = load.config.params.get("heat_loss_w_per_k")
        if outdoor is None or loss is None:
            return None
        return max(0.0, float(loss) * (self.comfort(load, ctx).target - outdoor))

    def expected_draw_w(self, load: Load, ctx: LoadCtx) -> float | None:
        """Electrical draw for that heat through the COP curve, capped at rated."""
        demand_w = self.heat_demand_w(load, ctx)
        outdoor = self.outdoor_c(ctx)
        if demand_w is None or outdoor is None:
            return None
        rated_w = float(load.config.params.get("nameplate_w", 3000.0))
        return curve_of(load).draw_w(demand_w, outdoor, rated_w)

    def reservation_w(self, load: Load, ctx: LoadCtx) -> float:
        """Return what D6 should hold back for this pump (§5.14).

        Measured plus a margin, or the curve's expectation, whichever is larger -
        and the rated power when nothing can be read, because a unit we cannot
        measure is one we must assume the worst of.
        """
        rated_w = float(load.config.params.get("nameplate_w", 3000.0))
        measured = ctx.reads.value(Role.POWER)
        expected = self.expected_draw_w(load, ctx)
        if measured is None:
            return rated_w
        floor_w = measured * (1.0 + RESERVE_MARGIN_FRACTION)
        return min(rated_w, max(floor_w, 0.0 if expected is None else expected))

    # ------------------------------------------------------------------ demand #

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the pump wants, measured against comfort (§5.14)."""
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
        return Demand(
            wants=wants or forced,
            required_kwh=required,
            deadline=deadline,
            min_w=0.0,
            max_w=self.reservation_w(load, ctx),
            urgency=urgency,
            comfort=comfort,
            price_sensitive=not comfort.violated and not forced,
            reason=_reason(self, load, state, ctx, comfort, wants=wants, forced=forced),
        )

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the kind needs, bounded by the band (INV-29, §5.4)."""
        params = load.config.params
        comfort = self.comfort(load, ctx)
        band_k = float(params.get("band_k", 1.0))
        stage = 0 if grant is None else grant.stage
        defrosting = self.defrosting(load, state, ctx)
        # §5.4: coast a kelvin under target from stage 3 - an offset, not a shed.
        # Never while defrosting: the unit is drawing and heating nothing, and
        # lowering its target there buys nothing back (INV-29).
        offset = -1.0 if stage >= _COAST_STAGE and not defrosting else 0.0
        ceiling = comfort.target + band_k
        if comfort.ceiling is not None:
            ceiling = min(ceiling, comfort.ceiling)
        return KindCtx(
            now=ctx.now,
            reads=ctx.reads,
            electrical=ctx.electrical,
            phases=load.config.phases,
            mode=mode,
            stage=stage,
            shed=False if grant is None or defrosting else grant.shed,
            stop_ok=False,
            blunt=False if grant is None else grant.blunt,
            target=comfort.target,
            # The band's bottom, never under the configured floor: INV-29 bounds
            # the setpoint to `comfort ± band` *then* the device's own limits, and
            # the band moves with the target when presence does.
            floor=max(comfort.floor, comfort.target - band_k),
            ceiling=ceiling,
            setpoint_delta=ctx.setpoint_delta + offset,
            desired=None if defrosting else ctx.desired,
            comfort_violated=comfort.violated,
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
            on_at_w=load.config.nameplate_w,
        )


def curve_at(stored: Mapping[str, float], outdoor_c: float) -> float:
    """Return the COP a stored curve gives at `outdoor_c` - for the review step."""
    return CopCurve.from_mapping({float(key): float(value) for key, value in stored.items()}).at(
        outdoor_c
    )


def _reason(
    device_type: HeatPump,
    load: Load,
    state: LoadState,
    ctx: LoadCtx,
    comfort: ComfortState,
    *,
    wants: bool,
    forced: bool,
) -> str:
    """One line for the snapshot and the log."""
    if device_type.defrosting(load, state, ctx):
        return "defrosting: drawing, not heating (INV-29)"
    if comfort.violated:
        return f"below the {comfort.floor:.1f} °C floor"
    if forced:
        return "forced"
    if not wants:
        return f"at {comfort.target:.1f} °C"
    if comfort.current is None:
        return "room temperature unknown"
    return f"{comfort.deficit:.1f} K under {comfort.target:.1f} °C"


TYPE = register(HeatPump())
