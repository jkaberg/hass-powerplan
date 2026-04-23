"""D10 §9 14 - the forecast-source registry (D10 §3, §6).

The added §9 item: extension is by registry, not by conditional, and
the registry is what the site flow renders its "what was found" review from. In
this WP it ships **empty**: `weather_entity` and `recorder_baseline` are HA
adapters and register themselves when `providers/forecasts/` lands in WP5.1, the
same way `providers/prices/formats/` registers into D1's table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    assert registry.keys_for(ForecastKind.BASELINE) == ("test_baseline",)
    assert registry.keys_for(ForecastKind.WEATHER) == ()


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
    """The v1 sources are HA adapters and arrive with `providers/forecasts/`."""
    registered = registry.keys()
    assert "weather_entity" not in registered
    assert "recorder_baseline" not in registered


async def test_14e_the_protocol_is_satisfied_by_the_registered_class() -> None:
    """`fetch(horizon, now)` returns a `Series` of the source's own kind (D10 §3)."""
    now = datetime(2026, 1, 13, 18, 0, tzinfo=UTC)
    series = await registry.build("test_baseline", {}).fetch(timedelta(hours=24), now)

    assert series.kind is ForecastKind.BASELINE
    assert series.source == "test_baseline"
    assert series.at(now + timedelta(hours=1)) is not None
    assert series.at(now - timedelta(hours=1)) is None
