"""`ev` - a car on a charger (D4 §6.2, §5.11).

Four questions and a battery size, because there is no car database: the number
is on the spec sheet and asking is simpler and always right (D4 §11).

What this type owns, and what every line of it came out of the reference house:

* **connected** is a status in the charger's connected set, and `offline` is not
  `disconnected` - a link loss is never "somebody unplugged the car", and folding
  the two together would have the controller go quiet at exactly the moment it
  lost sight of a 32 A load;
* the **session-done latch**, because `completed` is not a connected status: one
  night the car finished at 01:23, the allocator granted 0 W without
  authorising a stop, and an unauthorised zero was read as a hold - switch on,
  16 A standing, until morning;
* **min SoC now**: below the floor the car charges as fast as the capacity axis
  allows, regardless of price, and the price stops having a vote (INV-25 is about
  a zero grant; this is about the plan not getting one);
* the deadline, from the weekday departure table.

The 6 A cliff, the ramp and the write suppression are the `MODULATE` kind's
(INV-28); the stop that only the allocator may authorise is D6's (INV-39). The
one stop this type takes on itself is parking a session the *car* ended.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ...model import Demand, Grant, Mode, Urgency
from ..base import Load, LoadConfig, LoadCtx, LoadState, SessionDone, gate_config
from ..kinds.base import ControlKind, KindCtx, Role
from ..kinds.modulate import EV_MIN_A, Modulate, ModulateCfg
from ..questionnaire import Answers, Derived, Option, QCtx, Question, QuestionKind, Questionnaire
from ..stores.energy import EnergyStore
from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..stores.base import StoreModel

__all__ = [
    "BLOCKED_AFTER_S",
    "BLOCKED_W",
    "CONNECTED_STATUSES",
    "DERIVATION_VERSION",
    "EV_DONE_SOC_HYST",
    "OFFLINE_STATUSES",
    "Ev",
]

_LOGGER = logging.getLogger(__name__)

#: Bumped whenever a default below changes (INV-66).
DERIVATION_VERSION: Final = 1

#: How far SoC must fall below the SoC *at latch time* to re-arm (D4 §5.11).
EV_DONE_SOC_HYST: Final = 3.0

#: A car is on the cable in all of these. `completed` is deliberately here - the
#: car is plugged in, it has simply finished - and the latch, not the status set,
#: is what stops the next tick wanting it back. `de_authorizing` too: the charger
#: is revoking an RFID authorisation *with the cable in* (D-0281).
CONNECTED_STATUSES: frozenset[str] = frozenset(
    {
        "awaiting_start",
        "awaiting_authorization",
        "de_authorizing",
        "charging",
        "ready_to_charge",
        "completed",
        "paused",
        "car_connected",
    }
)

#: Granted and enabled, and still drawing nothing after this long: the charger is
#: blocked by something powerplan does not control, and says so once (§5.11).
BLOCKED_AFTER_S: Final = 180.0
#: Below this the charger is not charging, whatever the standby electronics draw.
BLOCKED_W: Final = 100.0

#: No contact with the charger. Not "unplugged" (README).
OFFLINE_STATUSES: frozenset[str] = frozenset({"offline", "unavailable", "unknown", "error"})

#: The charger maxima a flow offers, in amps (D4 §6.2).
_CHARGER_MAX_A = (10.0, 13.0, 16.0, 20.0, 25.0, 32.0, 40.0, 48.0, 63.0)

#: Weekdays Monday–Friday at 07:00 - a typical commute (D4 §6.2).
_DEFAULT_DEPARTURES: Mapping[str, str] = {str(day): "07:00" for day in range(5)}


def _phases_from_site(ctx: QCtx) -> int:
    """Return the phase count: single-phase unless the site says otherwise."""
    return 1 if ctx.phases is None else min(3, max(1, ctx.phases))


QUESTIONNAIRE = Questionnaire(
    questions=(
        Question(
            key="capacity_kwh",
            kind=QuestionKind.NUMBER,
            default=64.0,
            unit="kWh",
            min=5.0,
            max=250.0,
            help_key="ev_capacity",
        ),
        Question(
            key="max_a",
            kind=QuestionKind.CHOICE,
            options=tuple(Option(value=f"{amps:.0f}") for amps in _CHARGER_MAX_A),
            default="32",
            unit="A",
            help_key="ev_charger_max",
        ),
        Question(
            key="phases",
            kind=QuestionKind.CHOICE,
            options=(Option(value="1"), Option(value="3")),
            default=lambda ctx: str(_phases_from_site(ctx)),
            help_key="ev_phases",
        ),
        Question(
            key="target_soc",
            kind=QuestionKind.NUMBER,
            default=80.0,
            unit="%",
            min=10.0,
            max=100.0,
            help_key="ev_target_soc",
        ),
        Question(
            key="min_soc_now",
            kind=QuestionKind.NUMBER,
            default=20.0,
            unit="%",
            min=0.0,
            max=90.0,
            help_key="ev_min_soc",
        ),
        Question(
            key="departures",
            kind=QuestionKind.WEEKLY_TIME,
            default=dict(_DEFAULT_DEPARTURES),
            help_key="ev_departures",
        ),
        Question(
            key="soc_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            help_key="ev_soc_entity",
        ),
        Question(
            key="charge_eff",
            kind=QuestionKind.NUMBER,
            default=0.90,
            min=0.5,
            max=1.0,
            advanced=True,
            help_key="ev_efficiency",
        ),
        Question(
            key="min_a",
            kind=QuestionKind.NUMBER,
            default=EV_MIN_A,
            unit="A",
            min=6.0,
            max=16.0,
            advanced=True,
            help_key="ev_min_a",
        ),
        Question(
            key="step_up_a",
            kind=QuestionKind.NUMBER,
            default=4.0,
            unit="A",
            min=1.0,
            max=32.0,
            advanced=True,
            help_key="ev_step_up",
        ),
        Question(
            key="settle_s",
            kind=QuestionKind.NUMBER,
            default=60.0,
            unit="s",
            min=0.0,
            max=600.0,
            advanced=True,
            help_key="ev_settle",
        ),
        Question(
            key="suppress_delta_a",
            kind=QuestionKind.NUMBER,
            default=2.0,
            unit="A",
            min=0.0,
            max=10.0,
            advanced=True,
            help_key="ev_suppress_delta",
        ),
        Question(
            key="suppress_stale_s",
            kind=QuestionKind.NUMBER,
            default=60.0,
            unit="s",
            min=0.0,
            max=3600.0,
            advanced=True,
            help_key="ev_suppress_stale",
        ),
        Question(
            key="force_max_h",
            kind=QuestionKind.NUMBER,
            default=6.0,
            unit="h",
            min=0.5,
            max=24.0,
            advanced=True,
            help_key="ev_force_max_h",
        ),
        Question(
            key="calendar_entity",
            kind=QuestionKind.ENTITY,
            default=None,
            advanced=True,
            help_key="ev_calendar",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class Ev:
    """A charger with a car on it (D4 §6.2)."""

    key: ClassVar[str] = "ev"
    kinds: ClassVar[tuple[str, ...]] = ("modulate",)
    strategies: ClassVar[tuple[str, ...]] = ("deadline_fill", "cheapest_hours", "always")
    default_strategy: ClassVar[str] = "deadline_fill"
    questionnaire: ClassVar[Questionnaire] = QUESTIONNAIRE

    # ------------------------------------------------------------------ derive #

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Answers → parameters (D4 §6.2)."""
        max_a = float(answers.choice("max_a"))
        phases = int(answers.choice("phases"))
        # 230 V line-to-line is what a Norwegian IT charger sees; the site's own
        # profile replaces this the moment the load is built (D3 §5.1).
        nameplate_w = max_a * (230.0 if phases == 1 else 400.0 * 1.732)
        params: dict[str, Any] = {
            "capacity_kwh": answers.number("capacity_kwh"),
            "max_a": max_a,
            "min_a": answers.number("min_a"),
            "phases": phases,
            "nameplate_w": round(nameplate_w, 1),
            "target_soc": answers.number("target_soc"),
            "min_soc_now": answers.number("min_soc_now"),
            "charge_eff": answers.number("charge_eff"),
            "step_up_a": answers.number("step_up_a"),
            "settle_s": answers.number("settle_s"),
            "suppress_delta_a": answers.number("suppress_delta_a"),
            "suppress_stale_s": answers.number("suppress_stale_s"),
            "force_max_h": answers.number("force_max_h"),
            "departures": dict(answers.get("departures") or {}),
            "soc_entity": answers.get("soc_entity"),
            "calendar_entity": answers.get("calendar_entity"),
        }
        return Derived(
            params=params,
            strategy=self.default_strategy,
            strategy_params={"target_soc": params["target_soc"]},
            # The first load off the ladder: an EV has no comfort to lose, and a
            # deferred kilowatt-hour costs nothing but patience
            # (`design/DECISIONS.md` D-0067).
            priority=10,
            group=None,
            explanation_key="ev_review",
            explanation_params={
                "capacity_kwh": params["capacity_kwh"],
                "max_a": max_a,
                "phases": phases,
                "nameplate_w": params["nameplate_w"],
                "target_soc": params["target_soc"],
                "min_soc_now": params["min_soc_now"],
                "departures": params["departures"],
            },
            derivation_version=DERIVATION_VERSION,
        )

    # ------------------------------------------------------------------- build #

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime load: the modulating kind and the energy store."""
        kind = self._kind(cfg)
        return Load(
            config=cfg,
            kind=kind,
            device_type=self,
            gate=gate_config(kind, cfg),
            store=store if store is not None else self._store(cfg),
        )

    def _kind(self, cfg: LoadConfig) -> ControlKind:
        """Return the `MODULATE` kind in amps, with the cliff (§5.3, INV-28)."""
        params = cfg.params
        return Modulate(
            ModulateCfg(
                unit="a",
                min_value=float(params.get("min_a", EV_MIN_A)),
                max_value=float(params.get("max_a", 32.0)),
                step=1.0,
                cliff=True,
                step_up=float(params.get("step_up_a", 4.0)),
                settle_s=float(params.get("settle_s", 60.0)),
                suppress_delta=float(params.get("suppress_delta_a", 2.0)),
                suppress_stale_s=float(params.get("suppress_stale_s", 60.0)),
            )
        )

    def _store(self, cfg: LoadConfig) -> EnergyStore:
        """Return the car's battery (§5.7)."""
        params = cfg.params
        return EnergyStore(
            capacity_kwh=float(params.get("capacity_kwh", 64.0)),
            min_soc=float(params.get("min_soc_now", 20.0)),
            max_soc=float(params.get("target_soc", 80.0)),
            max_charge_w=cfg.nameplate_w,
            charge_eff=float(params.get("charge_eff", 0.90)),
        )

    # -------------------------------------------------------------------- tick #

    def status(self, ctx: LoadCtx) -> str | None:
        """Return the charger's status, lowercased, or `None` when nothing reports it."""
        text = ctx.reads.text(Role.STATUS)
        return None if text is None else text.strip().lower()

    def connected(self, ctx: LoadCtx) -> bool | None:
        """Whether a car is on the cable; `None` when the link is down (§5.11)."""
        status = self.status(ctx)
        if status is None or status in OFFLINE_STATUSES:
            return None
        return status in CONNECTED_STATUSES

    def soc(self, ctx: LoadCtx) -> float | None:
        """Return the car's state of charge, or `None` - never a pretended zero."""
        return ctx.reads.value(Role.SOC)

    def max_a_now(self, load: Load, ctx: LoadCtx) -> float:
        """Return the lowest limit anything readable imposes (§5.11).

        A sensor that cannot be read constrains nothing: `unknown` arriving as
        0 A must never clamp the charger to a standstill.
        """
        limits = [float(load.config.params.get("max_a", 32.0))]
        for role in (Role.CURRENT_MAX, Role.CABLE_RATING, Role.CIRCUIT_MAX):
            value = ctx.reads.value(role)
            if value is not None and value > 0.0:
                limits.append(value)
        return min(limits)

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Return the type's own conclusions about what the charger said (§5.11)."""
        return self._note_blocked(load, self._latch_session(load, state, ctx), ctx)

    def _latch_session(  # noqa: PLR0911 - two ways in and four ways out, each its own line
        self, load: Load, state: LoadState, ctx: LoadCtx
    ) -> LoadState:
        """Set and clear the session-done latch (§5.11).

        Four clears, each chosen so it cannot fire twice on the same state: the
        car is unplugged, a force goes off → on, the target is raised above the
        target *at latch time*, or SoC falls `EV_DONE_SOC_HYST` below the SoC at
        latch time.
        """
        params = load.config.params
        target = float(params.get("target_soc", 80.0))
        status = self.status(ctx)
        connected = self.connected(ctx)
        soc = self.soc(ctx)
        latch = state.session_done

        if latch is not None:
            if connected is False:
                return replace(state, session_done=None)
            if state.force_since is not None and state.force_since > latch.at:
                return replace(state, session_done=None)
            if latch.target_soc is not None and target > latch.target_soc:
                return replace(state, session_done=None)
            if soc is not None and latch.soc is not None and soc <= latch.soc - EV_DONE_SOC_HYST:
                return replace(state, session_done=None)
            return state

        if connected is not True:
            return state
        if status == "completed":
            return replace(
                state,
                session_done=SessionDone(
                    at=ctx.now, target_soc=target, soc=soc, reason="completed"
                ),
            )
        if soc is not None and soc >= target:
            return replace(
                state,
                session_done=SessionDone(
                    at=ctx.now, target_soc=target, soc=soc, reason="target_soc"
                ),
            )
        return state

    def _note_blocked(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Notice a charger that is granted, enabled and drawing nothing (§5.11).

        "Granted" is what the entities show: an armed limit at or above the
        minimum and the enable on. After `BLOCKED_AFTER_S` of that with no power
        the charger is blocked by something powerplan does not steer - an RFID
        it waits for, a queue, a fuse - and the reason its `blocked_by` sensor
        gives is logged once per reason, never once per tick.
        """
        params = load.config.params
        held = ctx.reads.value(Role.CURRENT_SET)
        enabled = _as_on(ctx.reads.current_of(Role.ENABLE))
        power = ctx.reads.value(Role.POWER)
        granted = (
            self.connected(ctx) is True
            and enabled
            and held is not None
            and held >= float(params.get("min_a", EV_MIN_A))
        )
        idle = power is not None and power < BLOCKED_W
        if not (granted and idle):
            if state.blocked_since is None and state.blocked_reason is None:
                return state
            return replace(state, blocked_since=None, blocked_reason=None)
        since = state.blocked_since or ctx.now
        if (ctx.now - since).total_seconds() < BLOCKED_AFTER_S:
            return state if state.blocked_since is not None else replace(state, blocked_since=since)
        reason = ctx.reads.text(Role.BLOCKED_BY) or "no reason reported"
        if reason != state.blocked_reason:
            _LOGGER.warning(
                "%s: granted %.0f A and enabled, drawing nothing for %.0f s — charging blocked by %s",
                load.load_id,
                held or 0.0,
                (ctx.now - since).total_seconds(),
                reason,
            )
        return replace(state, blocked_since=since, blocked_reason=reason)

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what the car wants, and how badly (§5.11)."""
        params = load.config.params
        target = float(params.get("target_soc", 80.0))
        min_soc_now = float(params.get("min_soc_now", 20.0))
        connected = self.connected(ctx)
        soc = self.soc(ctx)
        done = state.session_done is not None
        forced = state.mode is Mode.FORCE

        w_per_amp = ctx.electrical.w_per_amp(load.config.phases)
        max_w = self.max_a_now(load, ctx) * w_per_amp
        min_w = float(params.get("min_a", EV_MIN_A)) * w_per_amp

        wants = connected is True and not done and (soc is None or soc < target)
        deadline = self.next_departure(load, ctx)
        below_floor = soc is not None and soc < min_soc_now

        if connected is None:
            urgency = Urgency.NONE
            reason = "link lost: offline is not disconnected"
        elif connected is False:
            urgency = Urgency.NONE
            reason = "no car"
        elif done:
            urgency = Urgency.NONE
            reason = f"session done ({state.session_done.reason if state.session_done else ''})"
        elif below_floor:
            urgency = Urgency.MIN_SOC
            reason = f"under the {min_soc_now:.0f} % floor"
        elif not wants:
            urgency = Urgency.NONE
            reason = f"at or above {target:.0f} %"
        elif deadline is not None:
            urgency = Urgency.DEADLINE
            reason = f"{target:.0f} % by {deadline:%H:%M}"
        else:
            urgency = Urgency.NORMAL
            reason = f"charging to {target:.0f} %"
        if state.blocked_reason is not None:
            reason = f"{reason}; blocked by {state.blocked_reason}"

        required = (
            None
            if load.store is None
            else load.store.required_kwh(soc, target, deadline, ctx.store_ctx())
        )
        return Demand(
            wants=wants or (forced and connected is True),
            required_kwh=required,
            deadline=deadline,
            min_w=min_w,
            max_w=max_w,
            urgency=urgency,
            comfort=None,
            price_sensitive=not forced and urgency is not Urgency.MIN_SOC,
            reason=reason,
        )

    def next_departure(self, load: Load, ctx: LoadCtx) -> datetime | None:
        """Return the next departure: the weekday table or the bound calendar, whichever is first (§5.11, §6.2).

        A calendar event is a departure the household wrote down for one day; the
        table is every week's. The earlier of the two is the deadline, so a
        05:30 trip in the calendar beats the table's 07:00 and a calendar with
        nothing in it changes nothing (D-0281).
        """
        candidates = [
            event.start.astimezone(ctx.now.tzinfo)
            for event in ctx.calendar
            if event.start > ctx.now
        ]
        table = load.config.params.get("departures") or {}
        zone: tzinfo | None = ctx.zone if ctx.zone is not None else ctx.now.tzinfo
        if table and zone is not None:
            local = ctx.now.astimezone(zone)
            for ahead in range(8):
                day = local + timedelta(days=ahead)
                raw = table.get(str(day.weekday()))
                if raw is None:
                    continue
                at = _at_time(day, raw)
                if at > local:
                    candidates.append(at.astimezone(ctx.now.tzinfo))
                    break
        return min(candidates) if candidates else None

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Return what only the type knows: the session, the enable state, the limits."""
        params = load.config.params
        held = ctx.reads.value(Role.CURRENT_SET)
        enabled = _as_on(ctx.reads.current_of(Role.ENABLE))
        status = self.status(ctx)
        min_a = float(params.get("min_a", EV_MIN_A))
        session_active = (
            self.connected(ctx) is True
            and enabled
            and (status == "charging" or (held is not None and held >= min_a))
        )
        return KindCtx(
            now=ctx.now,
            reads=ctx.reads,
            electrical=ctx.electrical,
            phases=load.config.phases,
            mode=mode,
            stage=0 if grant is None else grant.stage,
            shed=False if grant is None else grant.shed,
            stop_ok=False if grant is None else grant.stop_ok,
            # The one stop the allocator does not have to authorise: a session
            # the car itself ended (§5.11).
            park=state.session_done is not None,
            blunt=False if grant is None else grant.blunt,
            session_active=session_active,
            enabled=enabled,
            max_value=self.max_a_now(load, ctx),
            held=held,
            on_at_w=load.config.nameplate_w,
        )


def _at_time(day: datetime, raw: str | time) -> datetime:
    """`raw` on `day`, in `day`'s own zone."""
    if isinstance(raw, time):
        moment = raw
    else:
        hour, minute = (int(part) for part in str(raw).split(":", 1))
        moment = time(hour=hour, minute=minute)
    return day.replace(hour=moment.hour, minute=moment.minute, second=0, microsecond=0)


def _as_on(value: Any) -> bool:
    """Whether a switch reads as on, whatever the entity spelled it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0.0
    return str(value).strip().lower() in {"on", "true", "1"}


TYPE = register(Ev())
