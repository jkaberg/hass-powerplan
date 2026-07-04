"""D10 §9 14 - the forecast-source registry (D10 §3, §6).

The added §9 item: extension is by registry, not by conditional, and
the registry is what the site flow renders its "what was found" review from.
`core/forecasts/registry.py` itself ships **empty**: `weather_entity` and
`recorder_baseline` are HA adapters, registering themselves from
`providers/forecasts/`, the same way `providers/prices/formats/`
registers into D1's table - once that package is imported anywhere in a test
session, `registry.keys()` legitimately carries them, so `test_14d` checks
`registry.py`'s own source text rather than runtime state (immune to import
order, the same reasoning `test_single_writer.py`'s grep tests use).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

from custom_components.powerplan.core.forecasts import (
    ForecastKind,
    ForecastSource,
    Series,
    SeriesPoint,
    registry,
)
from custom_components.powerplan.core.pricing.model import Field, FieldKind, Schema


@registry.register
class _FakeBaseline:
    """A source that answers from a constant - the shape a provider has to fit."""

    key: ClassVar[str] = "test_baseline"
    kind: ClassVar[ForecastKind] = ForecastKind.BASELINE
    schema: ClassVar[Schema] = (Field(key="watts", kind=FieldKind.NUMBER, default=500.0),)

    def __init__(self, watts: float = 500.0) -> None:
        self.watts = watts

    async def fetch(self, horizon: timedelta, now: datetime) -> Series:
        """Return one point covering the whole horizon."""
        return Series(
            kind=self.kind,
            unit="W",
            points=(SeriesPoint(start=now, end=now + horizon, value=self.watts, confidence=0.9),),
            source=self.key,
            issued_at=now,
        )


def test_14_a_registered_source_is_found_by_key_and_by_kind() -> None:
    """The registry answers with the entry's schema, so the flow can render it."""
    registered = registry.keys()
    assert "test_baseline" in registered
    entry = registry.entry("test_baseline")

    assert entry.kind is ForecastKind.BASELINE
    assert entry.schema[0].key == "watts"
    # Membership, not equality: `recorder_baseline` may already be
    # registered too, in a session that also imported `providers/forecasts/`.
    assert "test_baseline" in registry.keys_for(ForecastKind.BASELINE)
    assert "test_baseline" not in registry.keys_for(ForecastKind.WEATHER)


def test_14b_the_registry_builds_a_source_from_saved_options() -> None:
    """`build(key, options)` is what the runtime calls per configured source."""
    source: ForecastSource = registry.build("test_baseline", {"watts": 750.0})

    assert isinstance(source, _FakeBaseline)
    assert source.watts == 750.0


def test_14c_an_unknown_key_is_a_lookup_error() -> None:
    """A key nobody registered is a bug in configuration, not a silent default."""
    with pytest.raises(KeyError):
        registry.entry("solcast")


def test_14d_core_ships_no_home_assistant_sources() -> None:
    """The v1 sources are HA adapters, registered by `providers/forecasts/`, not `core/`.

    Checked against `registry.py`'s own source text, not `registry.keys()`:
    once `providers/forecasts/` is imported anywhere in the same test
    session, `weather_entity`/`recorder_baseline` are legitimately present -
    this test's real claim is about which *file* registers them, immune to
    import order the way `tests/core/invariants/test_single_writer.py`'s
    own grep tests are.
    """
    source = Path(registry.__file__).read_text(encoding="utf-8")
    assert "@register" not in source, "registry.py declares the mechanism, never uses it itself"
    assert "import homeassistant" not in source


async def test_14e_the_protocol_is_satisfied_by_the_registered_class() -> None:
    """`fetch(horizon, now)` returns a `Series` of the source's own kind (D10 §3)."""
    now = datetime(2026, 1, 13, 18, 0, tzinfo=UTC)
    series = await registry.build("test_baseline", {}).fetch(timedelta(hours=24), now)

    assert series.kind is ForecastKind.BASELINE
    assert series.source == "test_baseline"
    assert series.at(now + timedelta(hours=1)) is not None
    assert series.at(now - timedelta(hours=1)) is None
