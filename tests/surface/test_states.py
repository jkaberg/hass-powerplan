"""WP U.1's entity half (D8 §5.15 entities table; review ENT-1, ENT-4, ENT-23, ENT-24).

States from closed, translated sets - never an internal code (R2, R4):

* `sensor.<site>_advice` is an `enum` over D2's advice keys plus `all_good`; its
  state is the most severe item and never `top_entries`, which is data (ENT-1);
* `event.<site>` has a translation key and translated event types (ENT-4);
* `sensor.<load>_session` is `no_car · waiting · charging · done`, read from the
  snapshot, with the engine's free-form reason kept as an attribute (ENT-23).

The translations themselves are checked by `tests/flows/test_text.py` (§9 18).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.core.tariffs.evaluator import Advice
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.load_entities import SESSION_STATES, session_state
from custom_components.powerplan.sensor import ADVICE_STATES, advice_state
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _advice(key: str, severity: str = "info") -> Advice:
    return Advice(key=key, severity=severity, params={})  # type: ignore[arg-type]


def test_ent_1_the_state_is_the_most_severe_advice_and_never_top_entries() -> None:
    """`top_entries` is always first and always data; a warning outranks any info."""
    assert advice_state(None) == "all_good"
    assert advice_state([_advice("top_entries")]) == "all_good"
    assert advice_state([_advice("top_entries"), _advice("step_headroom")]) == "step_headroom"
    assert (
        advice_state(
            [
                _advice("top_entries"),
                _advice("step_headroom"),
                _advice("coarse_history", "warn"),
            ]
        )
        == "coarse_history"
    )
    assert set(ADVICE_STATES) >= {"all_good", "step_headroom", "coarse_history"}


def test_ent_1_the_advice_sensor_is_an_enum(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """On a live site: `device_class: enum`, its options, and a state among them."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "advice"))
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == list(ADVICE_STATES)
    assert state.state in ADVICE_STATES
    assert "items" in state.attributes


def test_ent_4_the_event_entity_is_named_and_its_types_translated(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """A translation key, "Events" as its name, every `EventKind` an event type."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("event", DOMAIN, unique_id(SITE_ENTRY_ID, "events"))
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.translation_key == "events"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["event_types"] == [kind.value for kind in EventKind]


@dataclass
class _Status:
    """The parts of a `LoadStatus` the session reads."""

    load_id: str = "ev"
    granted_w: float = 0.0
    measured_w: float | None = None
    demand: Any = field(default_factory=lambda: SimpleNamespace(wants=False, reason="no car"))
    latches: Any = field(default_factory=lambda: SimpleNamespace(session_done=False))


def _runtime(connected: str | None) -> Any:
    edges = {} if connected is None else {"connected:ev": connected}
    return SimpleNamespace(state=SimpleNamespace(events=SimpleNamespace(edges=edges)))


def test_ent_23_the_session_is_a_closed_set_from_the_snapshot() -> None:
    """No car · waiting · charging · done; never the engine's English reason (R2)."""
    assert SESSION_STATES == ("no_car", "waiting", "charging", "done")
    wanting = SimpleNamespace(wants=True, reason="charging to 80 %")
    full = SimpleNamespace(wants=False, reason="at or above 80 %")
    done = SimpleNamespace(session_done=True)

    assert session_state(_Status(), _runtime("0")) == "no_car"
    assert session_state(_Status(), _runtime(None)) == "no_car"
    assert session_state(_Status(demand=wanting), _runtime("1")) == "waiting"
    assert session_state(_Status(demand=wanting, granted_w=7000.0), _runtime("1")) == "charging"
    assert (
        session_state(_Status(demand=wanting, granted_w=7000.0, measured_w=0.0), _runtime("1"))
        == "waiting"
    ), "a grant the car does not draw is not charging"
    assert session_state(_Status(demand=full), _runtime("1")) == "done"
    assert session_state(_Status(latches=done), _runtime("1")) == "done"
