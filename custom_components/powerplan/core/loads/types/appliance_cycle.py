"""`appliance_cycle` - a dishwasher, a washing machine, a dryer (D4 §5.13, §6.8).

A cycle is one contiguous run of a fixed programme. The household loads the
machine and asks for it to be ready by a time; `run_once` (D5 §5.6) picks the
cheapest start that fits; the allocator grants the programme's reservation; this
type starts the machine, watches it run, and learns what the programme actually
cost so the next reservation is the machine's and not the label's.

Four things the type stands for:

* **INV-59** - a running cycle is not shed at stages 1–3. Cutting power to a
  dishwasher mid-wash does not pause it, it aborts it, and the whole programme
  runs again from zero (`tests/sim/cycle.py` says so because the reference
  house's does). Only a blunt stage-4 reason - a fuse, a contract - may cut it.
* **The default profile until the first run**, then a **learned** one bounded
  against the default (0.5–2× energy and duration), because a label is a claim
  and a measurement is a fact, while a single bad measurement is not (INV-63's
  spirit, D4 §2).
* **Detection without a vocabulary.** A `PROGRAM_STATE` text is one integration's
  words; the type reads *running* from the power draw first and the text second,
  and *finished* from a long idle after a run, so a machine with no programme
  entity at all still works (`design/DECISIONS.md` D-0207).
* **A request is state, not a mode.** `request()` records that the household
  wants a run; `force` is the same request with the price ignored (D-0208).
"""

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import ComfortState, Demand, Grant, Mode, Urgency
from ..base import (
    CyclePhase,
    CycleProfile,
    CycleState,
    Load,
    LoadConfig,
    LoadCtx,
    LoadState,
    gate_config,
)
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.switch import Switch, SwitchCfg
from ..questionnaire import Answers, Derived, Option, QCtx, Question, QuestionKind, Questionnaire
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "BLUNT_STAGE",
    "DERIVATION_VERSION",
    "IDLE_FINISHED_S",
    "LEARN_BOUNDS",
    "PROGRAMMES",
    "RUNNING_W",
    "SEGMENTS",
    "ApplianceCycle",
    "Programme",
    "profile_of",
]

#: Bumped whenever a table below changes (INV-66).
DERIVATION_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Programme:
    """What one programme costs by its label (D4 §6.8)."""

    energy_kwh: float
    duration_min: float
    peak_w: float
    label: str


#: D4 §6.8's table. `peak_w` is what the heater draws while it heats - the number
#: a relay must be able to carry and the default nameplate; sources: EU energy
#: label eco-programme typicals (energy, duration), the label's connected load
#: (peak) - `design/DECISIONS.md` D-0207.
PROGRAMMES: Mapping[str, Programme] = {
    "dishwasher_eco": Programme(energy_kwh=0.9, duration_min=180.0, peak_w=2000.0, label="eco"),
    "washing_machine_40": Programme(
        energy_kwh=0.7, duration_min=120.0, peak_w=2000.0, label="40 °C"
    ),
    "dryer_heat_pump": Programme(energy_kwh=1.5, duration_min=150.0, peak_w=900.0, label="cotton"),
    "dryer_condenser": Programme(energy_kwh=3.0, duration_min=120.0, peak_w=2500.0, label="cotton"),
}

#: Ten segments per run - the shape D6 reserves against (D4 §4.1).
SEGMENTS: Final = 10

#: A learned value must lie within these multiples of the default, else the
#: run is not learned from (D4 §2, D-0207).
LEARN_BOUNDS: Final = (0.5, 2.0)

#: Above this the machine is running, whatever its programme entity says.
RUNNING_W: Final = 20.0

#: A run that has drawn nothing for this long is finished (drying looks like
#: finished; a real finish is quiet for good).
IDLE_FINISHED_S: Final = 300.0

#: A started machine that never draws within this window did not start.
START_TIMEOUT_S: Final = 600.0

#: Programme-state words that mean "not running", across integrations.
_IDLE_WORDS: Final = frozenset(
    {"", "idle", "off", "ready", "finished", "aborted", "standby", "unavailable", "unknown"}
)

#: The ladder stage from which a running cycle may be cut (INV-59, D6 §5.4).
BLUNT_STAGE: Final = 4


def _start_control_default(ctx: QCtx) -> str:
    """Return what the profile detected: a start service, a delay timer, else a plug."""
    if "start" in ctx.capabilities:
        return "start_program"
    if "delay_timer" in ctx.capabilities:
        return "delay_timer"
    return "switch"


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="appliance",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=name) for name in PROGRAMMES),
            default="dishwasher_eco",
            help_key="cycle_appliance",
        ),
        Question(
            key="ready_by",
            kind=QuestionKind.TIME,
            default="07:00",
            help_key="cycle_ready_by",
        ),
        Question(
            key="start_control",
            kind=QuestionKind.CHOICE,
            options=(
                Option(value="switch"),
                Option(value="start_program"),
                Option(value="delay_timer"),
            ),
            default=_start_control_default,
            help_key="cycle_start_control",
        ),
        Question(
            key="power_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="cycle_power_entity",
        ),
        Question(
            key="energy_kwh",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="kWh",
            min=0.1,
            max=10.0,
            advanced=True,
            help_key="cycle_energy",
        ),
        Question(
            key="duration_min",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="min",
            min=15.0,
            max=600.0,
            advanced=True,
            help_key="cycle_duration",
        ),
        Question(
            key="power_w",
            kind=QuestionKind.NUMBER,
            derived_default=True,
            unit="W",
            min=100.0,
            max=5000.0,
            advanced=True,
            help_key="cycle_power",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="cycle_force_max_h",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class ApplianceCycle:
    """A dishwasher, a washing machine, a tumble dryer: one programme, one block."""

    key: ClassVar[str] = "appliance_cycle"
    kinds: ClassVar[tuple[str, ...]] = ("switch",)
    strategies: ClassVar[tuple[str, ...]] = ("run_once", "always")
    default_strategy: ClassVar[str] = "run_once"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters, every number sourced (D4 §6.8)."""
        appliance = answers.choice("appliance")
        programme = PROGRAMMES[appliance]
        energy_kwh = _or(answers.get("energy_kwh"), programme.energy_kwh)
        duration_min = _or(answers.get("duration_min"), programme.duration_min)
        nameplate_w = _or(answers.get("power_w"), programme.peak_w)
        start_control = answers.choice("start_control")
        ready = answers.get("ready_by")
        ready_by = ready.strftime("%H:%M") if isinstance(ready, time) else str(ready or "07:00")
        shape = tuple(1.0 / SEGMENTS for _ in range(SEGMENTS))

        params: dict[str, Any] = {
            "kind": "switch",
            "store": None,
            "appliance": appliance,
            "programme": programme.label,
            "ready_by": ready_by,
            "start_control": start_control,
            "start_role": "start" if start_control != "switch" else "switch",
            "power_entity": answers.get("power_entity"),
            "nameplate_w": nameplate_w,
            "energy_kwh": energy_kwh,
            "duration_s": duration_min * 60.0,
            "shape": list(shape),
            "force_max_h": answers.number("force_max_h"),
            "substitutable": False,
            "phases": 1,
        }
        return Derived(
            params=params,
            strategy=self.default_strategy,
            strategy_params={
                "duration_min": duration_min,
                "profile": list(shape),
                "ready_by": ready_by,
            },
            # Below the thermal loads and the EV: a late wash is an annoyance,
            # a cold bathroom is a complaint (D4 §6.8).
            priority=20,
            group="appliances",
            explanation_key="cycle_review",
            explanation_params={
                "appliance": appliance,
                "energy_kwh": energy_kwh,
                "duration_min": duration_min,
                "ready_by": ready_by,
                "start_control": start_control,
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: a relay or a start command, no store (§5.13)."""
        kind = self._kind(cfg)
        return Load(
            config=cfg, kind=kind, device_type=self, gate=gate_config(kind, cfg), store=store
        )

    def _kind(self, cfg: LoadConfig) -> ControlKind:
        """`SWITCH` on the plug, or on the start role for a machine with a start service."""
        params = cfg.params
        role = Role.START if params.get("start_role") == "start" else Role.SWITCH
        return Switch(
            SwitchCfg(
                on_at_w=self.profile(cfg, None).mean_w,
                # The dwell is the programme: nothing toggles a running machine.
                min_on_s=float(params.get("duration_s", 10800.0)),
                min_off_s=0.0,
                min_interval_s=60.0,
                # §5.10: a running cycle is urgent only at stage 4 (INV-59).
                urgent_from_stage=4,
                role=role,
            )
        )

    # ----------------------------------------------------------------- profile #

    def profile(self, cfg: LoadConfig, state: LoadState | None) -> CycleProfile:
        """Return the learned profile if one exists, else §6.8's default."""
        if state is not None and state.cycle is not None and state.cycle.profile is not None:
            return state.cycle.profile
        params = cfg.params
        shape = tuple(float(v) for v in params.get("shape", ()))
        return CycleProfile(
            duration_s=float(params.get("duration_s", 10800.0)),
            energy_kwh=float(params.get("energy_kwh", 0.9)),
            shape=shape or tuple(1.0 / SEGMENTS for _ in range(SEGMENTS)),
            learned=False,
            samples=0,
        )

    # ----------------------------------------------------------------- request #

    def request(self, state: LoadState, now: datetime) -> LoadState:
        """Record that the household wants a run (`button.run_now`, D8 §5.5)."""
        cycle = state.cycle or CycleState()
        if cycle.active:
            return state
        return replace(
            state,
            cycle=replace(
                cycle,
                phase=CyclePhase.IDLE,
                requested_at=now,
                start_at=None,
                started_at=None,
                finished_at=None,
                energy_kwh=0.0,
                segments=(),
                idle_since=None,
                sampled_at=None,
            ),
        )

    def requested(self, state: LoadState) -> bool:
        """Whether a run is wanted and not yet finished."""
        cycle = state.cycle
        if cycle is None:
            return False
        return cycle.requested_at is not None and cycle.phase is not CyclePhase.FINISHED

    def ready_by(self, load: Load, state: LoadState, ctx: LoadCtx) -> datetime | None:
        """Return the next ready-by instant after the request, in the site's zone (HLD §7.1)."""
        cycle = state.cycle
        if cycle is None or cycle.requested_at is None:
            return None
        hh, mm = (int(part) for part in str(load.config.params.get("ready_by", "07:00")).split(":"))
        zone = ctx.zone or ctx.now.tzinfo
        local = cycle.requested_at.astimezone(zone)
        candidate = datetime.combine(local.date(), time(hh, mm), tzinfo=zone)
        if candidate <= local:
            candidate = datetime.combine(
                local.date() + timedelta(days=1), time(hh, mm), tzinfo=zone
            )
        return candidate

    # -------------------------------------------------------------------- tick #

    def level(self, load: Load, ctx: LoadCtx) -> float | None:
        """Return `None`: a cycle has no level, it is done or it is not."""
        return None

    def comfort(self, load: Load, ctx: LoadCtx) -> ComfortState:
        """Return an empty band: a cycle carries a deadline, not a comfort target."""
        return ComfortState(
            current=None,
            target=0.0,
            floor=0.0,
            ceiling=None,
            violated=False,
            deficit=0.0,
            direction="heat",
        )

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the machine wants: its programme, once, by the ready-by time (§5.13)."""
        cycle = state.cycle or CycleState()
        profile = self.profile(load.config, state)
        forced = state.mode is Mode.FORCE
        wants = self.requested(state) or forced or cycle.active
        remaining = (
            max(0.0, profile.energy_kwh - cycle.energy_kwh) if cycle.active else profile.energy_kwh
        )
        deadline = self.ready_by(load, state, ctx) if wants else None
        if cycle.active or forced:
            urgency = Urgency.DEADLINE
        elif wants:
            urgency = Urgency.NORMAL if deadline is None else Urgency.DEADLINE
        else:
            urgency = Urgency.NONE
        return Demand(
            wants=wants,
            required_kwh=remaining if wants else None,
            deadline=deadline,
            min_w=profile.mean_w if cycle.active else 0.0,
            max_w=load.config.nameplate_w,
            urgency=urgency,
            comfort=self.comfort(load, ctx),
            # A running cycle is committed: the price stopped mattering when it
            # started (INV-59). A forced one never cared.
            price_sensitive=not cycle.active and not forced,
            reason=_reason(cycle, wants=wants, forced=forced),
        )

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Advance the cycle: start, run, finish, learn; or abort (§5.13)."""
        cycle = state.cycle or CycleState()
        power = ctx.reads.value(Role.POWER)
        text = (ctx.reads.text(Role.PROGRAM_STATE) or "").strip().lower()
        drawing = power is not None and power >= RUNNING_W
        says_running = text not in _IDLE_WORDS
        running_now = drawing or (power is None and says_running)
        profile = self.profile(load.config, state)

        if cycle.phase in {CyclePhase.IDLE, CyclePhase.PLANNED, CyclePhase.STARTED}:
            if running_now and (
                cycle.requested_at is not None
                or state.mode is Mode.FORCE
                or cycle.phase is CyclePhase.STARTED
            ):
                cycle = replace(
                    cycle,
                    phase=CyclePhase.RUNNING,
                    started_at=cycle.started_at or ctx.now,
                    energy_kwh=0.0,
                    segments=tuple(0.0 for _ in range(SEGMENTS)),
                    sampled_at=ctx.now,
                    idle_since=None,
                )
            elif (
                cycle.phase is CyclePhase.STARTED
                and cycle.started_at is not None
                and (ctx.now - cycle.started_at).total_seconds() > START_TIMEOUT_S
            ):
                # The start command went out and nothing happened: back to the queue.
                cycle = replace(cycle, phase=CyclePhase.IDLE, started_at=None)
            return replace(state, cycle=cycle)

        if cycle.phase is CyclePhase.RUNNING:
            return replace(
                state, cycle=self._run(cycle, profile, ctx, power=power, drawing=drawing, text=text)
            )

        if cycle.phase is CyclePhase.ABORTED and running_now:
            # It was restarted (by us or by hand): a new run from zero.
            cycle = replace(
                cycle,
                phase=CyclePhase.RUNNING,
                started_at=ctx.now,
                energy_kwh=0.0,
                segments=tuple(0.0 for _ in range(SEGMENTS)),
                sampled_at=ctx.now,
                idle_since=None,
            )
        return replace(state, cycle=cycle)

    def _run(
        self,
        cycle: CycleState,
        profile: CycleProfile,
        ctx: LoadCtx,
        *,
        power: float | None,
        drawing: bool,
        text: str,
    ) -> CycleState:
        """Integrate the run, then decide finished / aborted / still running."""
        started = cycle.started_at or ctx.now
        elapsed = (ctx.now - started).total_seconds()
        # Energy, over real time (D4 §4.1).
        if cycle.sampled_at is not None and power is not None:
            dt_h = max(0.0, (ctx.now - cycle.sampled_at).total_seconds()) / 3600.0
            kwh = power * dt_h / 1000.0
            bucket = min(
                SEGMENTS - 1, max(0, int(elapsed / max(profile.duration_s, 1.0) * SEGMENTS))
            )
            segments = list(cycle.segments or (0.0,) * SEGMENTS)
            segments[bucket] += kwh
            cycle = replace(cycle, energy_kwh=cycle.energy_kwh + kwh, segments=tuple(segments))
        cycle = replace(cycle, sampled_at=ctx.now)

        if text == "aborted":
            return replace(cycle, phase=CyclePhase.ABORTED, idle_since=None)
        says_running = text not in _IDLE_WORDS
        if drawing or (text and says_running):
            # Drawing, or a programme entity still naming a segment: the 5 W of
            # residual-heat drying is a running dishwasher, not a finished one.
            return replace(cycle, idle_since=None)
        idle_since = cycle.idle_since or ctx.now
        finished_text = (
            text in {"finished", "off", "idle", "ready"} and elapsed >= 0.5 * profile.duration_s
        )
        idle_long = (ctx.now - idle_since).total_seconds() >= IDLE_FINISHED_S
        if finished_text or (idle_long and elapsed >= 0.5 * profile.duration_s):
            return self._finish(cycle, profile, ctx)
        if idle_long:
            # Quiet long before the programme could be done: the power was cut.
            return replace(cycle, phase=CyclePhase.ABORTED, idle_since=None)
        return replace(cycle, idle_since=idle_since)

    def _finish(self, cycle: CycleState, profile: CycleProfile, ctx: LoadCtx) -> CycleState:
        """Close the run and learn from it, bounded against the default (D4 §2)."""
        started = cycle.started_at or ctx.now
        duration = (ctx.now - started).total_seconds()
        learned = cycle.profile if cycle.profile is not None else None
        default = (
            profile
            if not profile.learned
            else CycleProfile(
                duration_s=profile.duration_s, energy_kwh=profile.energy_kwh, shape=profile.shape
            )
        )
        lo, hi = LEARN_BOUNDS
        within = (
            lo * default.duration_s <= duration <= hi * default.duration_s
            and lo * default.energy_kwh <= cycle.energy_kwh <= hi * default.energy_kwh
        )
        if within and cycle.energy_kwh > 0.0:
            total = sum(cycle.segments) or cycle.energy_kwh
            shape = tuple(round(s / total, 4) for s in cycle.segments)
            samples = (learned.samples if learned is not None else 0) + 1
            # A running mean over the learned runs, so one odd run moves it a little.
            prev_e = (
                learned.energy_kwh if learned is not None and learned.learned else cycle.energy_kwh
            )
            prev_d = learned.duration_s if learned is not None and learned.learned else duration
            n = samples
            learned = CycleProfile(
                duration_s=prev_d + (duration - prev_d) / n,
                energy_kwh=prev_e + (cycle.energy_kwh - prev_e) / n,
                shape=shape,
                learned=True,
                samples=samples,
            )
        return replace(
            cycle,
            phase=CyclePhase.FINISHED,
            finished_at=ctx.now,
            requested_at=None,
            idle_since=None,
            profile=learned,
            runs=cycle.runs + 1,
        )

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Everything the relay needs - with a running cycle immune below stage 4 (INV-59)."""
        cycle = state.cycle or CycleState()
        stage = 0 if grant is None else grant.stage
        blunt = False if grant is None else grant.blunt
        shed = False if grant is None else grant.shed
        if cycle.active and stage < BLUNT_STAGE and not blunt:
            shed = False
        return KindCtx(
            now=ctx.now,
            reads=ctx.reads,
            electrical=ctx.electrical,
            phases=load.config.phases,
            mode=mode,
            stage=stage,
            shed=shed,
            stop_ok=False if grant is None else grant.stop_ok,
            blunt=blunt,
            target=None,
            floor=None,
            ceiling=None,
            setpoint_delta=0.0,
            desired=ctx.desired,
            comfort_violated=False,
            session_active=cycle.active,
            held=load.kind.current(ctx.reads),
            last_restore_at=state.last_target_restore_at,
            # A running machine keeps its relay whatever the grant says short of
            # stage 4: the threshold it is judged against is zero while it runs.
            on_at_w=0.0
            if cycle.active and stage < BLUNT_STAGE and not blunt
            else self.profile(load.config, state).mean_w,
        )


def _or(value: Any, fallback: float) -> float:
    """Return the answered number, or the derived default when it was left blank."""
    return fallback if value is None else float(value)


def _reason(cycle: CycleState, *, wants: bool, forced: bool) -> str:
    """One line for the snapshot and the log."""
    if cycle.phase is CyclePhase.RUNNING:
        return f"running, {cycle.energy_kwh:.2f} kWh so far"
    if cycle.phase is CyclePhase.STARTED:
        return "start sent"
    if cycle.phase is CyclePhase.ABORTED:
        return "aborted — will run again from the start"
    if forced:
        return "forced"
    if wants:
        return "requested"
    return "idle"


TYPE = register(ApplianceCycle())


def profile_of(load: Load) -> CycleProfile:
    """Return the cycle's default profile - duration, shape, energy - as materialised (INV-66).

    `state=None`: the default branch of `profile()`, never a live learned one -
    D11's accounting shadow reads a load's *effective* parameters once, at add
    time (`accounting_hook.py::params_of`), the same as every other kind's, and
    none of them chase a live-updating value yet (D10's fit gate is not wired to
    accounting for any kind, INV-63's promise). A fresh `ApplianceCycle()`, not
    `TYPE`: the registry's own `register()` returns the generic `DeviceType`
    protocol, which does not name `profile()`.
    """
    return ApplianceCycle().profile(load.config, None)
