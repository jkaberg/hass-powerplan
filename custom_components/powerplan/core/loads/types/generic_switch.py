"""`generic_switch` - the load that is just a relay (D4 §6.7).

A pool pump, a sauna, a hot tub, a ventilation unit, or whatever else the
household has on a plug. No store, no comfort target, no temperature: what it has
is a nameplate, an on/off, and an opinion about when it wants to run - which is
the strategy's business and not this type's.

§6.7's table in two columns: what the thing *is* gives its nameplate and its
default strategy. A pool pump and a ventilation unit want *hours per day* and do
not care which ones (`cheapest_hours`); a sauna and a hot tub want to be hot when
somebody says so, which is `always` plus the `force` switch every load has.

The nameplate numbers are the sizes those appliances are sold in; a bound power
sensor always wins over them (`design/DECISIONS.md` D-0206). `inverted` lives here
because a normally-closed relay is a plug thing, and getting it backwards on a
sauna is the one mistake in this module that matters.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, gate_config
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.switch import Switch, SwitchCfg
from ..questionnaire import Answers, Derived, Option, QCtx, Question, QuestionKind, Questionnaire
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = ["APPLIANCES", "DERIVATION_VERSION", "Appliance", "GenericSwitch"]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Appliance:
    """What a kind of plug load implies (D4 §6.7).

    `hours_per_day` is `None` for the two that are not a daily quota: a sauna is
    heated when somebody is going to sit in it.
    """

    watts: float
    strategy: str
    hours_per_day: float | None
    priority: int


#: D4 §6.7's table, with the nameplates §6.7 left to the code
#: (`design/DECISIONS.md` D-0206):
#:
#: * pool pump 750 W - a domestic filter pump is 0.4–1.1 kW;
#: * sauna 6 kW - the standard heater for a 6–8 m³ domestic cabin;
#: * hot tub 2 kW - the heater of a 230 V, 13 A spa plus its circulation;
#: * ventilation 150 W - the two fans of a balanced unit at normal speed;
#: * other 1 kW - a 10 A plug load, the neutral guess.
#:
#: The priorities put every one of them under the thermal loads (30 and up): a
#: pool that filters at 04:00 instead of 16:00 has lost nothing. Ventilation is
#: the exception, because air is closer to comfort than a pool is.
APPLIANCES: Mapping[str, Appliance] = {
    "pool_pump": Appliance(watts=750.0, strategy="cheapest_hours", hours_per_day=8.0, priority=15),
    "sauna": Appliance(watts=6000.0, strategy="always", hours_per_day=None, priority=15),
    "hot_tub": Appliance(watts=2000.0, strategy="always", hours_per_day=None, priority=18),
    "ventilation": Appliance(
        watts=150.0, strategy="cheapest_hours", hours_per_day=20.0, priority=25
    ),
    "other": Appliance(watts=1000.0, strategy="always", hours_per_day=None, priority=15),
}

#: The HA area or device name, lowercased, matched against the options.
_NAME_TOKENS: Mapping[str, str] = {
    "pool": "pool_pump",
    "basseng": "pool_pump",
    "sauna": "sauna",
    "badstu": "sauna",
    "tub": "hot_tub",
    "spa": "hot_tub",
    "boble": "hot_tub",
    "vent": "ventilation",
    "aggregat": "ventilation",
}


def _appliance_default(ctx: QCtx) -> str:
    """Prefill from the device or area name where it says so (HLD §7.9)."""
    haystack = f"{ctx.device_name or ''} {ctx.area or ''}".strip().lower()
    for token, appliance in _NAME_TOKENS.items():
        if token in haystack:
            return appliance
    return "other"


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="appliance",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in APPLIANCES),
            default=_appliance_default,
            help_key="generic_switch_appliance",
        ),
        Question(
            key="power_w",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="W",
            min=5.0,
            max=20000.0,
            help_key="generic_switch_power",
        ),
        Question(
            key="power_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="generic_switch_power_entity",
        ),
        Question(
            key="hours_per_day",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="h",
            min=0.0,
            max=24.0,
            help_key="generic_switch_hours_per_day",
        ),
        Question(
            key="min_on_s",
            kind=QuestionKind.NUMBER,
            # A pump that starts and stops every tick wears out; fifteen minutes
            # is the shortest run worth having (`design/DECISIONS.md` D-0206).
            default=900.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="generic_switch_min_on",
        ),
        Question(
            key="min_off_s",
            kind=QuestionKind.NUMBER,
            default=900.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="generic_switch_min_off",
        ),
        Question(
            key="inverted",
            kind=QuestionKind.BOOL,
            default=False,
            advanced=True,
            help_key="generic_switch_inverted",
        ),
        Question(
            key="command_interval_s",
            kind=QuestionKind.NUMBER,
            default=120.0,
            unit="s",
            min=0.0,
            max=7200.0,
            advanced=True,
            help_key="generic_switch_interval",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            # A sauna is the reason this is asked: the one load whose force is
            # routinely three hours long.
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="generic_switch_force_max_h",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class GenericSwitch:
    """Anything on a relay: a pool pump, a sauna, a hot tub, a fan (D4 §6.7)."""

    key: ClassVar[str] = "generic_switch"
    kinds: ClassVar[tuple[str, ...]] = ("switch",)
    strategies: ClassVar[tuple[str, ...]] = (
        "cheapest_hours",
        "always",
        "opportunistic",
        "observe",
    )
    default_strategy: ClassVar[str] = "cheapest_hours"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, with every number sourced (D4 §6.7)."""
        key = answers.choice("appliance")
        appliance = APPLIANCES[key]
        nameplate_w = _or(answers.get("power_w"), appliance.watts)
        hours_per_day = answers.get("hours_per_day")
        if hours_per_day is None:
            hours_per_day = appliance.hours_per_day
        params: dict[str, Any] = {
            "kind": "switch",
            "store": None,
            "appliance": key,
            "nameplate_w": nameplate_w,
            "power_entity": answers.get("power_entity"),
            "hours_per_day": hours_per_day,
            "min_on_s": answers.number("min_on_s"),
            "min_off_s": answers.number("min_off_s"),
            "inverted": answers.flag("inverted"),
            "command_interval_s": answers.number("command_interval_s"),
            "force_max_h": answers.number("force_max_h"),
            "substitutable": False,
            "phases": 1,
        }
        strategy_params: dict[str, Any] = {}
        if appliance.strategy == "cheapest_hours" and hours_per_day is not None:
            strategy_params["hours_per_day"] = float(hours_per_day)
        return Derived(
            params=params,
            strategy=appliance.strategy,
            strategy_params=strategy_params,
            priority=appliance.priority,
            group=None,
            explanation_key="generic_switch_review",
            explanation_params={
                "appliance": key,
                "nameplate_w": nameplate_w,
                "strategy": appliance.strategy,
                "hours_per_day": hours_per_day,
                "inverted": params["inverted"],
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: one relay, no store (§6.7, HLD §6.4)."""
        kind = self._kind(cfg)
        return Load(
            config=cfg,
            kind=kind,
            device_type=self,
            gate=gate_config(kind, cfg),
            store=store,
        )

    def _kind(self, cfg: LoadConfig) -> ControlKind:
        """Return the `SWITCH` kind, with the dwell clocks and `inverted` (§5.6)."""
        params = cfg.params
        return Switch(
            SwitchCfg(
                on_at_w=float(params.get("nameplate_w", 1000.0)),
                min_on_s=float(params.get("min_on_s", 900.0)),
                min_off_s=float(params.get("min_off_s", 900.0)),
                inverted=bool(params.get("inverted", False)),
                min_interval_s=float(params.get("command_interval_s", 120.0)),
                urgent_from_stage=3,
            )
        )

    # -------------------------------------------------------------------- tick #

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the relay wants: to run, when the plan lets it (§6.7).

        There is no comfort here and no store: a pool pump would draw power now
        if it were allowed to, all day, and which hours it gets is the strategy's
        answer. `required_kwh` is `None` - not zero - because this type cannot
        compute one, and a zero would tell the planner the job was done.
        """
        measured = ctx.reads.value(Role.POWER)
        nameplate_w = load.config.nameplate_w
        forced = state.mode is Mode.FORCE
        return Demand(
            wants=True,
            required_kwh=None,
            deadline=None,
            min_w=0.0,
            max_w=nameplate_w if measured is None else max(measured, nameplate_w),
            urgency=Urgency.NORMAL,
            comfort=None,
            price_sensitive=not forced,
            reason="forced"
            if forced
            else f"{load.config.params.get('appliance', 'other')} on call",
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Return the state unchanged: a relay remembers nothing we need."""
        return state

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the kind needs: the grant, the plan, and the nameplate."""
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
            desired=ctx.desired,
            held=load.kind.current(ctx.reads),
            on_at_w=load.config.nameplate_w,
        )


def _or(value: Any, fallback: float) -> float:
    """Return the answered number, or the derived default when it was left blank."""
    return fallback if value is None else float(value)


TYPE = register(GenericSwitch())
