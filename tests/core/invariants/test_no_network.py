"""D9 §9 14 - no test in the PR suite opens a socket; every source fixture names its capture.

The harness refuses a connection to anything outside the test host (the Home
Assistant pytest plugin's socket guard, over `pytest-socket`), so an adapter that
reached for the network in a test would fail it (D9 §5.15, D13 §19 14). This
pins the guard, so a change to the plugin cannot quietly lift it.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.plugins import HASocketBlockedError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tariff_sources"


def test_14_a_socket_to_the_internet_is_refused() -> None:
    """Opening a socket raises before any packet leaves (INV-73's test half)."""
    with pytest.raises(HASocketBlockedError):
        socket.create_connection(("1.1.1.1", 443), timeout=1)
    # The guard also fails a test that tried; this one tried on purpose.
    HASocketBlockedError.instances.clear()


def test_14_every_tariff_source_fixture_names_its_capture() -> None:
    """Each source's captures say where and when they were taken (D9 §5.15)."""
    directories = [path for path in FIXTURES.iterdir() if path.is_dir()]
    assert directories
    for directory in directories:
        assert (directory / "CAPTURED").is_file() or (directory / "COMMIT").is_file(), directory
