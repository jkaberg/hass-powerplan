"""D4 §9 6 - link loss forgets the last written limit; `offline ≠ disconnected`.

The one thing that is easy to forget, and the reason this test exists: when the
Bluetooth link drops, powerplan must **not** go on believing the limit it last
wrote is still in force. The write may have been lost on the way out, and the
charger may have fallen back to its own configured maximum - 32 A - while we were
blind (the charger's README; `loads.py::_link_lost`). So a reconnect
re-arms from scratch instead of comparing a grant against a number nobody can
vouch for.

And `offline` is not `disconnected`. The cloud integration had no such value;
`easee_ble` does, it means "no contact with the charger", and folding the two
together would have the controller quietly conclude the car had been unplugged at
exactly the moment it lost sight of a 32 A load.

The profile expresses both as `Reads`, because that is all the pure core takes: a
role nothing can vouch for is `available = False` with no reading, which is row 4
of the gate matrix (a transient, then a failure) and never a value row 3 can call
"the same" (INV-22, INV-23).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.powerplan.core.loads import Action, LoadCtx, Mode, Reads, Role
from custom_components.powerplan.core.loads.gate import (
    Command,
    GateState,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.core.loads.types.ev import Ev
from custom_components.powerplan.providers.profiles import (
    BoundDevice,
    DeviceView,
    SessionState,
    easee_ble,
)
from tests.core.loads.conftest import REFERENCE_PROFILE, modulate_kind
from tests.providers.profiles.conftest import LIMIT, STATUS, dump_view

#: The night of the twelve dropped sessions, well away from `HH:00:00` (HLD §7.1).
NOW = datetime(2026, 9, 13, 23, 41, 7, tzinfo=UTC)

#: What the charger falls back to, on its own, while nobody is looking.
CHARGER_OWN_MAX_A = 32.0


def bound(view: DeviceView) -> BoundDevice:
    """Bind the profile to a matched view, as the subentry will (D4 §5.9)."""
    return easee_ble.PROFILE.bind(easee_ble.PROFILE.match(view).bindings)


def ctx(reads: Reads) -> LoadCtx:
    """Wrap `Reads` in the smallest `LoadCtx` the status questions need."""
    return LoadCtx(now=NOW, reads=reads, electrical=REFERENCE_PROFILE)


@pytest.mark.inv("INV-23")
def test_06_offline_is_a_lost_link_and_not_an_unplugged_car(easee: DeviceView) -> None:
    """`offline` → link down; `disconnected` → no car. Two facts, two states."""
    vocabulary = easee_ble.STATUSES

    assert vocabulary.state("offline") is SessionState.LINK_DOWN
    assert vocabulary.state("disconnected") is SessionState.DISCONNECTED
    assert not vocabulary.state("offline").connected
    assert not vocabulary.state("disconnected").connected
    assert vocabulary.state("offline").link_down
    assert not vocabulary.state("disconnected").link_down

    # A status entity that cannot answer at all is a lost link too, never an
    # unplugged car: blindness never opens a gate (INV-15, INV-17).
    assert vocabulary.state(None) is SessionState.LINK_DOWN
    assert vocabulary.state("unavailable") is SessionState.LINK_DOWN
    assert easee_ble.PROFILE.link_down(easee) is False


@pytest.mark.inv("INV-23")
def test_06b_link_loss_forgets_the_limit(easee: DeviceView) -> None:
    """The number still reads 10 A; nobody can vouch for it, so it is not read.

    The captured charger has 10 A armed. Once the status says `offline` the number
    entity's own value is worthless - the charger may have fallen back to 32 A
    while we were blind - so `CURRENT_SET` comes back unavailable and with no
    reading at all.
    """
    offline = dump_view("easee_ble_charger", states={STATUS: "offline"})

    live = bound(easee).reads(easee, NOW)
    lost = bound(offline).reads(offline, NOW)

    assert live.value(Role.CURRENT_SET) == 10.0
    assert live.available(Role.CURRENT_SET)

    assert lost.value(Role.CURRENT_SET) is None
    assert not lost.available(Role.CURRENT_SET)
    assert lost.current_of(Role.CURRENT_SET) is None, "nothing the gate can call 'the same'"
    assert easee_ble.PROFILE.link_down(offline) is True


def test_06c_a_disconnected_cable_keeps_the_limit_readable(easee: DeviceView) -> None:
    """`disconnected` is the ordinary resting state - the radio is fine.

    The captured charger *is* `disconnected`, with 10 A armed and the switch off.
    Forgetting the limit here would re-arm the charger every time somebody took
    the car to work.
    """
    reads = bound(easee).reads(easee, NOW)

    assert reads.value(Role.CURRENT_SET) == 10.0
    assert reads.available(Role.CURRENT_SET)
    assert reads.text(Role.STATUS) == "disconnected"
    assert easee_ble.PROFILE.link_down(easee) is False


@pytest.mark.inv("INV-23")
def test_06d_a_reconnect_re_arms_from_scratch(easee: DeviceView) -> None:
    """The forgetting has a consequence, and this is it (INV-22).

    Three ticks. The link is up and 16 A is already armed, so nothing is sent
    (row 3). The link drops: the limit is forgotten, so the decision is a
    transient rather than "the same" - there is nothing to compare against. The
    link returns with the charger on its own 32 A maximum, and 16 A goes out
    again, although `last_value` still says 16 A. Trusting what we remembered
    writing is exactly what would have left the charger at 32 A.
    """
    cfg = easee_ble.PROFILE.quirks().gate_config(modulate_kind())
    command = Command(writes=(Write(Role.CURRENT_SET, 16.0),), reason="plan: 16 A", want_on=True)
    remembered = GateState(last_value=16.0)

    armed = dump_view("easee_ble_charger", states={STATUS: "charging", LIMIT: "16.0"})
    reads = bound(armed).reads(armed, NOW)
    same = decide(
        command,
        current=reads.current_of(Role.CURRENT_SET),
        mode=Mode.AUTO,
        cfg=cfg,
        state=remembered,
        budget=TransportBudget.empty(),
        now=NOW,
        available=reads.available(Role.CURRENT_SET),
    )
    assert same.action is Action.SAME

    offline = dump_view("easee_ble_charger", states={STATUS: "offline", LIMIT: "16.0"})
    lost = bound(offline).reads(offline, NOW)
    blind = decide(
        command,
        current=lost.current_of(Role.CURRENT_SET),
        mode=Mode.AUTO,
        cfg=cfg,
        state=remembered,
        budget=TransportBudget.empty(),
        now=NOW,
        available=lost.available(Role.CURRENT_SET),
    )
    assert blind.action is Action.TRANSIENT, "an unreachable entity is a transient (INV-23)"
    assert blind.action is not Action.SAME, "the forgotten limit cannot be 'already held'"

    back = dump_view(
        "easee_ble_charger",
        states={STATUS: "charging", LIMIT: f"{CHARGER_OWN_MAX_A}"},
    )
    reads = bound(back).reads(back, NOW)
    rearmed = decide(
        command,
        current=reads.current_of(Role.CURRENT_SET),
        mode=Mode.AUTO,
        cfg=cfg,
        state=remembered,
        budget=TransportBudget.empty(),
        now=NOW,
        available=reads.available(Role.CURRENT_SET),
    )
    assert rearmed.action is Action.WRITTEN
    assert rearmed.value == 16.0
    assert rearmed.current == CHARGER_OWN_MAX_A


@pytest.mark.inv("INV-23")
def test_06e_the_core_reads_the_two_apart(easee: DeviceView) -> None:
    """`Ev.connected()` is `None` on a lost link and `False` on an empty cable.

    The distinction has to survive the profile: the type asks "is a car on the
    cable", and `None` - "I cannot see" - is what stops the controller concluding
    anything at all (D4 §5.11).
    """
    offline = dump_view("easee_ble_charger", states={STATUS: "offline"})
    charging = dump_view("easee_ble_charger", states={STATUS: "charging"})
    ev = Ev()

    assert ev.connected(ctx(bound(offline).reads(offline, NOW))) is None
    assert ev.connected(ctx(bound(easee).reads(easee, NOW))) is False
    assert ev.connected(ctx(bound(charging).reads(charging, NOW))) is True


def test_06f_the_blocked_reason_rides_along_with_the_loss(easee: DeviceView) -> None:
    """`charging_blocked_by` explains 34 ways a charger declines (D4 §5.11).

    It rides along with the link loss because that is the line somebody reads a
    night later: the captured charger says
    `secondary_unit_not_requesting_current`, which is "no car", not a fault.
    """
    reads = bound(easee).reads(easee, NOW)

    assert reads.text(Role.BLOCKED_BY) == "secondary_unit_not_requesting_current"
    assert len(reads.options(Role.BLOCKED_BY)) == 34
    assert easee_ble.PROFILE.blocked_by(easee) == "secondary_unit_not_requesting_current"
