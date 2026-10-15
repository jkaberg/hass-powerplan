"""The dashboard's lowered hours: a coast, an eco shed or a setpoint held under target (D-0507, D-0766).

On 14 Oct 2026 the reference house's heat pumps were planned 1 K under target
from 06:00 to 22:00, envelope `None`, and the dashboard drew nothing: only an
envelope of 0 counted as standing still.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.powerplan.core.model import Desired, PlanSlot
from custom_components.powerplan.runtime import _held_low

START = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def _slot(envelope_w: float | None, desired: Desired | float | None) -> PlanSlot:
    return PlanSlot(
        start=START, end=START + timedelta(minutes=15), envelope_w=envelope_w, desired_state=desired
    )


def test_standing_still_is_held_low() -> None:
    """Envelope 0 is a coast or a postponement (INV-30)."""
    assert _held_low(_slot(0.0, None))


def test_an_eco_shed_is_held_low() -> None:
    """A `mode` floor's eco shed, whatever its envelope."""
    assert _held_low(_slot(None, Desired.SHED))


def test_a_setpoint_under_target_is_held_low() -> None:
    """A heat pump eased off by its setpoint draws, at a lower level."""
    assert _held_low(_slot(None, -0.5))


def test_a_ramp_rounding_and_comfort_are_not() -> None:
    """Under 0.1 K is the rate limit's step, not a lowered hour; comfort and a bank are not."""
    assert not _held_low(_slot(None, -0.05))
    assert not _held_low(_slot(None, 0.0))
    assert not _held_low(_slot(None, 0.5))
    assert not _held_low(_slot(None, Desired.COMFORT))
    assert not _held_low(_slot(3500.0, None))
