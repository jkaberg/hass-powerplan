"""`battery` - a home battery behind an inverter (D4 §6.6).

The one load whose power is **signed**: it charges (import +) and it discharges
(export −), and both ends are bounded by the inverter's limits and by a reserve
the household keeps for an outage. The type is the store model, the questionnaire
and the signed `MODULATE` kind over the inverter's power setpoint; the strategies
that make it earn its keep - `arbitrage` and `peak_shave` - are D5's (built with
PLAN WP5.4, together with the ladder's discharge placement in D6 §5.3).

What the type stands for:

* **The reserve is a floor.** Below `reserve_pct` the battery is not a source; a
  plan that discharged into it would be selling the household's outage margin
  for a few øre (INV-55's shape, applied to SoC).
* **Usable capacity comes from the chemistry.** LFP is run 95 % of nameplate,
  NMC 90 % - the label's kWh is what the cells hold, not what they should cycle
  (D4 §6.6; sources in `CHEMISTRIES`).
* **Grid charging is a household choice**, not the planner's: with it off the
  battery charges from surplus only (v1.x, D5 `surplus`), and `max_w` is 0 for
  the allocator until then (`design/DECISIONS.md` D-0209).
* **Fail-safe** (INV-64): the release value is 0 W - an inverter left at zero
  neither drains nor overcharges, and the inverter's own controller resumes.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, gate_config
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.modulate import Modulate, ModulateCfg
from ..questionnaire import Answers, Derived, Option, QCtx, Question, QuestionKind, Questionnaire
from ..stores.energy import EnergyStore
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = ["CHEMISTRIES", "DERIVATION_VERSION", "Battery", "Chemistry"]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Chemistry:
    """What a cell chemistry implies for the usable window (D4 §6.6)."""

    usable_fraction: float
    charge_eff: float
    discharge_eff: float


#: D4 §6.6: LFP 95 %, NMC 90 % usable. Round-trip ≈ 90 % split evenly is the
#: usual inverter datasheet figure (source: D4 §6.6; `design/DECISIONS.md` D-0209).
CHEMISTRIES: Mapping[str, Chemistry] = {
    "lfp": Chemistry(usable_fraction=0.95, charge_eff=0.95, discharge_eff=0.95),
    "nmc": Chemistry(usable_fraction=0.90, charge_eff=0.95, discharge_eff=0.95),
}

#: The inverter setpoint step, in watts - a typical hybrid inverter's resolution.
POWER_STEP_W: Final = 100.0

#: How far above the reserve the store is asked to sit at least, so a discharge
#: never lands exactly on the floor.
_RESERVE_MARGIN_PCT: Final = 1.0

#: A three-phase inverter is offered three phases; anything else is one.
_THREE_PHASE: Final = 3


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="capacity_kwh",
            kind=QuestionKind.NUMBER,
            default=10.0,
            unit="kWh",
            min=1.0,
            max=200.0,
            help_key="battery_capacity",
        ),
        Question(
            key="max_charge_kw",
            kind=QuestionKind.NUMBER,
            default=5.0,
            unit="kW",
            min=0.5,
            max=50.0,
            help_key="battery_max_charge",
        ),
        Question(
            key="max_discharge_kw",
            kind=QuestionKind.NUMBER,
            default=5.0,
            unit="kW",
            min=0.5,
            max=50.0,
            help_key="battery_max_discharge",
        ),
        Question(
            key="reserve_pct",
            kind=QuestionKind.NUMBER,
            default=20.0,
            unit="%",
            min=0.0,
            max=90.0,
            help_key="battery_reserve",
        ),
        Question(
            key="allow_grid_charge",
            kind=QuestionKind.BOOL,
            default=True,
            help_key="battery_grid_charge",
        ),
        Question(
            key="chemistry",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in CHEMISTRIES),
            default="lfp",
            help_key="battery_chemistry",
        ),
        Question(
            key="soc_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="battery_soc_entity",
        ),
        Question(
            key="power_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="battery_power_entity",
        ),
        Question(
            key="max_soc",
            kind=QuestionKind.NUMBER,
            default=100.0,
            unit="%",
            min=50.0,
            max=100.0,
            advanced=True,
            help_key="battery_max_soc",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="battery_force_max_h",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class Battery:
    """A home battery: signed power, a reserve, a usable window."""

    key: ClassVar[str] = "battery"
    kinds: ClassVar[tuple[str, ...]] = ("modulate",)
    #: `peak_shave` claims discharge for a threatened ceiling first and lets
    #: `arbitrage` plan the rest - the safer default (D5 §5.8); `always`
    #: stays offered for a household that wants no price steering at all.
    strategies: ClassVar[tuple[str, ...]] = ("peak_shave", "arbitrage", "always")
    default_strategy: ClassVar[str] = "peak_shave"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, every number sourced (D4 §6.6)."""
        chemistry_key = answers.choice("chemistry")
        chemistry = CHEMISTRIES[chemistry_key]
        capacity = answers.number("capacity_kwh")
        max_charge_w = answers.number("max_charge_kw") * 1000.0
        max_discharge_w = answers.number("max_discharge_kw") * 1000.0
        reserve = answers.number("reserve_pct")
        max_soc = answers.number("max_soc")
        params: dict[str, Any] = {
            "kind": "modulate",
            "store": "energy",
            "chemistry": chemistry_key,
            "capacity_kwh": capacity,
            "usable_kwh": round(capacity * chemistry.usable_fraction, 2),
            "usable_fraction": chemistry.usable_fraction,
            "charge_eff": chemistry.charge_eff,
            "discharge_eff": chemistry.discharge_eff,
            "nameplate_w": max_charge_w,
            "max_charge_w": max_charge_w,
            "max_discharge_w": max_discharge_w,
            "reserve_soc": reserve,
            "min_soc": reserve + _RESERVE_MARGIN_PCT,
            "max_soc": max_soc,
            "allow_grid_charge": answers.flag("allow_grid_charge"),
            "soc_entity": answers.get("soc_entity"),
            "power_entity": answers.get("power_entity"),
            "power_step_w": POWER_STEP_W,
            "force_max_h": answers.number("force_max_h"),
            "substitutable": False,
            "phases": _THREE_PHASE if ctx.phases == _THREE_PHASE else 1,
        }
        return Derived(
            params=params,
            strategy=self.default_strategy,
            strategy_params={
                "reserve_soc": reserve,
                "max_soc": max_soc,
                "allow_grid_charge": answers.flag("allow_grid_charge"),
            },
            # Between the thermal loads and the EV: a battery is a means, not a
            # comfort, and the ladder discharges it before any comfort shed
            # (HLD §10 dec. 5).
            priority=30,
            group="storage",
            explanation_key="battery_review",
            explanation_params={
                "capacity_kwh": capacity,
                "usable_kwh": params["usable_kwh"],
                "chemistry": chemistry_key,
                "reserve_pct": reserve,
                "max_charge_kw": answers.number("max_charge_kw"),
                "max_discharge_kw": answers.number("max_discharge_kw"),
                "allow_grid_charge": answers.flag("allow_grid_charge"),
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: a signed setpoint over the inverter and the store."""
        kind = self._kind(cfg)
        return Load(
            config=cfg,
            kind=kind,
            device_type=self,
            gate=gate_config(kind, cfg),
            store=store if store is not None else self._store(cfg),
        )

    def _kind(self, cfg: LoadConfig) -> ControlKind:
        """Return the signed `MODULATE` kind in watts over `BATTERY_POWER_SET` (§4.2)."""
        params = cfg.params
        return Modulate(
            ModulateCfg(
                unit="w",
                min_value=-float(params.get("max_discharge_w", 5000.0)),
                max_value=float(params.get("max_charge_w", 5000.0)),
                step=float(params.get("power_step_w", POWER_STEP_W)),
                cliff=False,
                step_up=float(params.get("max_charge_w", 5000.0)),
                settle_s=30.0,
                suppress_delta=float(params.get("power_step_w", POWER_STEP_W)),
                suppress_stale_s=60.0,
                signed=True,
                tolerance=float(params.get("power_step_w", POWER_STEP_W)),
                min_interval_s=30.0,
                role=Role.BATTERY_POWER_SET,
                # An inverter takes a signed setpoint and nothing else; the
                # fail-safe is 0 W (INV-64, D-0209).
                enable_role=None,
                release_value=0.0,
            )
        )

    def _store(self, cfg: LoadConfig) -> EnergyStore:
        """Return the battery as a store, in percent (§4.3)."""
        params = cfg.params
        return EnergyStore(
            capacity_kwh=float(params.get("capacity_kwh", 10.0)),
            min_soc=float(params.get("min_soc", 21.0)),
            max_soc=float(params.get("max_soc", 100.0)),
            max_charge_w=float(params.get("max_charge_w", 5000.0)),
            usable_fraction=float(params.get("usable_fraction", 0.95)),
            charge_eff=float(params.get("charge_eff", 0.95)),
            discharge_eff=float(params.get("discharge_eff", 0.95)),
            reserve_soc=float(params.get("reserve_soc", 20.0)),
            max_discharge_w=float(params.get("max_discharge_w", 5000.0)),
        )

    # -------------------------------------------------------------------- tick #

    def soc(self, ctx: LoadCtx) -> float | None:
        """Return the state of charge in percent, if the inverter reports one."""
        return ctx.reads.value(Role.SOC)

    def level(self, load: Load, ctx: LoadCtx) -> float | None:
        """Return the SoC - the store's level is the battery's."""
        return self.soc(ctx)

    def comfort(self, load: Load, ctx: LoadCtx) -> ComfortState:
        """Return the reserve as the floor and the charge ceiling as the target."""
        params = load.config.params
        soc = self.soc(ctx)
        reserve = float(params.get("reserve_soc", 20.0))
        target = float(params.get("max_soc", 100.0))
        return ComfortState(
            current=soc,
            target=target,
            floor=reserve,
            ceiling=target,
            violated=soc is not None and soc < reserve,
            deficit=0.0 if soc is None else target - soc,
            direction="heat",
        )

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return the signed envelope: charge up to the ceiling, discharge down to the reserve."""
        params = load.config.params
        comfort = self.comfort(load, ctx)
        forced = state.mode is Mode.FORCE
        grid_charge = bool(params.get("allow_grid_charge", True))
        max_charge_w = float(params.get("max_charge_w", 5000.0))
        max_discharge_w = float(params.get("max_discharge_w", 5000.0))
        soc = comfort.current
        can_charge = soc is None or soc < comfort.target
        can_discharge = soc is not None and soc > comfort.floor + _RESERVE_MARGIN_PCT
        required = (
            None
            if load.store is None
            else load.store.required_kwh(soc, comfort.target, None, ctx.store_ctx())
        )
        wants = (can_charge and (grid_charge or forced)) or can_discharge
        if comfort.violated:
            urgency = Urgency.COMFORT_VIOLATION
        elif not wants:
            urgency = Urgency.NONE
        else:
            urgency = Urgency.NORMAL
        return Demand(
            wants=wants,
            required_kwh=required if can_charge else 0.0,
            deadline=None,
            # Signed: the allocator may grant a negative watt figure - discharge
            # - down to the inverter's limit, never below the reserve.
            min_w=-max_discharge_w if can_discharge else 0.0,
            max_w=max_charge_w if can_charge and (grid_charge or forced) else 0.0,
            urgency=urgency,
            comfort=comfort,
            price_sensitive=not comfort.violated and not forced,
            reason=_reason(comfort, grid_charge=grid_charge, forced=forced),
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Return the state unchanged: a battery has nothing to latch."""
        return state

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the setpoint kind needs, with the SoC bounds resolved."""
        comfort = self.comfort(load, ctx)
        return KindCtx(
            now=ctx.now,
            reads=ctx.reads,
            electrical=ctx.electrical,
            phases=load.config.phases,
            mode=mode,
            stage=0 if grant is None else grant.stage,
            shed=False if grant is None else grant.shed,
            stop_ok=True if grant is None else grant.stop_ok,
            blunt=False if grant is None else grant.blunt,
            target=comfort.target,
            floor=comfort.floor,
            ceiling=comfort.ceiling,
            setpoint_delta=0.0,
            desired=ctx.desired,
            comfort_violated=comfort.violated,
            session_active=True,
            max_value=float(load.config.params.get("max_charge_w", 5000.0)),
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
        )


def _reason(comfort: ComfortState, *, grid_charge: bool, forced: bool) -> str:
    """One line for the snapshot and the log."""
    if comfort.violated:
        return f"below the {comfort.floor:.0f} % reserve"
    if forced:
        return "forced"
    if comfort.current is None:
        return "SoC unknown"
    if comfort.current >= comfort.target:
        return f"full at {comfort.current:.0f} %"
    if not grid_charge:
        return f"{comfort.current:.0f} %, surplus only"
    return f"{comfort.current:.0f} % of {comfort.target:.0f} %"


TYPE = register(Battery())
