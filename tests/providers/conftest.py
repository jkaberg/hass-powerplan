"""Shared helpers for the provider tests (D9 §3, §5.1).

Everything under `tests/providers/` starts Home Assistant: a provider is the
only place a raw entity state becomes a number the core trusts, so it is tested
against a real `hass` with real `State` objects rather than against a stub whose
`last_reported` we could invent.

Captured dumps come from `tests/fixtures/captured/` (real entities of the
reference house, D9 §5.8); hand-written payloads for formats the house does not
run come from `tests/fixtures/formats/`, each naming its upstream documentation
in a `source` key (`design/DECISIONS.md` D-0081).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_json(*parts: str) -> dict[str, Any]:
    """Return the parsed fixture at `tests/fixtures/<parts>`."""
    data: dict[str, Any] = json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))
    return data


@pytest.fixture
def captured() -> Callable[[str], dict[str, Any]]:
    """Return a loader for a captured entity dump by its `name`."""

    def load(name: str) -> dict[str, Any]:
        return load_json("captured", f"{name}.json")

    return load


@pytest.fixture
def format_fixture() -> Callable[[str], dict[str, Any]]:
    """Return a loader for a hand-written format payload by its file stem."""

    def load(name: str) -> dict[str, Any]:
        return load_json("formats", f"{name}.json")

    return load


@pytest.fixture
def restore_capture(hass: HomeAssistant) -> Callable[[dict[str, Any]], None]:
    """Put every entity of a captured dump into the state machine."""

    def restore(dump: dict[str, Any]) -> None:
        for entity in dump["entities"]:
            hass.states.async_set(entity["entity_id"], entity["state"], entity["attributes"])

    return restore
