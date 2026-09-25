"""Session-wide test configuration (D9 §3).

`tests/core/` runs with no Home Assistant: nothing there requests `hass`, so
the autouse fixture below does nothing and no `HomeAssistant` instance is ever
created for a core test. `tests/providers/`, `tests/flows/` and `tests/e2e/`
request `hass` and therefore get `enable_custom_integrations`.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension

if TYPE_CHECKING:
    from collections.abc import Iterator

    from syrupy.assertion import SnapshotAssertion

pytest_plugins = ("pytest_homeassistant_custom_component",)

#: Measured seconds per xdist group and per slow ungrouped test (`tools/durations.py`).
DURATIONS = Path(__file__).with_name("durations.json")


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Keep snapshots in `snapshots/`, whichever plugin's `snapshot` pytest loaded last.

    syrupy and pytest-homeassistant-custom-component both define the fixture, and
    the plugins load in an order that differs between machines.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Load `custom_components/powerplan` for every test that starts HA."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture(autouse=True)
def only_the_tariff_sources_a_test_registers(request: pytest.FixtureRequest) -> Iterator[None]:
    """Show a test the tariff sources it registers, never a shipped adapter (D9 §5.15).

    The shipped adapters fetch the network; a test that wants one registers it and
    serves its captured documents (`tests/builders/tariff_sources.py`). The docs
    tests read the shipped registry and never fetch: they keep it (D14 §5.6).
    """
    from custom_components.powerplan.providers.tariffs import base  # noqa: PLC0415

    if "docs" in request.node.path.parts:
        yield
        return
    shipped = [base.get(key) for key in base.keys()]  # noqa: SIM118 - the registry's own call
    for cls in shipped:
        base.unregister(cls.key)
    yield
    for key in base.keys():  # noqa: SIM118 - the registry's own call
        base.unregister(key)
    for cls in shipped:
        base.register(cls)


def _group(item: pytest.Item) -> str | None:
    marker = item.get_closest_marker("xdist_group")
    if marker is None:
        return None
    return str(marker.args[0] if marker.args else marker.kwargs.get("name"))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Start the longest work first (D9 §5.13).

    The wall clock of a parallel run is bounded below by its longest sequential
    piece - a thirty-day scenario, the smoke benchmark - and pytest-xdist hands
    out work in collection order. So the xdist groups and the slow ungrouped
    tests `tests/durations.json` names move to the front, longest first; every
    other test keeps its place after them. Which tests run, and what they
    assert, does not change. Within a group the collection order is kept, so a
    group's module-scoped fixture is still built once.
    """
    if not DURATIONS.exists():
        return
    known = json.loads(DURATIONS.read_text(encoding="utf-8"))
    groups: dict[str, float] = known.get("groups", {})
    tests: dict[str, float] = known.get("tests", {})

    def weight(item: pytest.Item) -> float:
        group = _group(item)
        if group is not None:
            return groups.get(group, 0.0)
        return tests.get(item.nodeid, 0.0)

    order = {id(item): position for position, item in enumerate(items)}
    items.sort(key=lambda item: (-weight(item), order[id(item)]))
