"""D8 §9 9: the notification policy - dedupe, quiet hours, urgency, persistent ids, fallback."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components import persistent_notification as pn

from custom_components.powerplan.core.engine import Notification
from custom_components.powerplan.notifications import (
    NotificationPolicy,
    QuietHours,
    render,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

#: 10:00 UTC on a January day; the test zone's local time is hours behind.
DAY = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)


def _note(category: str = "peak_warning", key: str = "peak:w1", **params: Any) -> Notification:
    return Notification(category=category, key=key, params=params, severity="warn")


def _policy(hass: HomeAssistant, **overrides: Any) -> NotificationPolicy:
    config = {
        "peak_warning": {"transport": "persistent", "service": None},
        "comfort_violation": {"transport": "persistent", "service": None},
        "deadline_at_risk": {"transport": "persistent", "service": None},
        "device_unhealthy": {"transport": "notify", "service": "mobile_app_phone"},
        "level_up": {"transport": "off", "service": None},
    }
    config.update(overrides.pop("config", {}))
    return NotificationPolicy(hass, "e1", config=config, **overrides)


def _persistent_ids(hass: HomeAssistant) -> set[str]:
    return set(pn._async_get_or_create_notifications(hass))


async def test_09a_a_key_is_not_repeated_inside_its_category_interval(hass: HomeAssistant) -> None:
    """Peak warnings: once per hour per key; a new key is new."""
    policy = _policy(hass)
    assert (
        await policy.handle(_note(window_start="10:00", expected_kwh=9.9, ceiling_kwh=10), DAY)
        == "persistent"
    )
    assert (
        await policy.handle(
            _note(window_start="10:00", expected_kwh=9.9, ceiling_kwh=10),
            DAY + timedelta(minutes=30),
        )
        is None
    )
    assert (
        await policy.handle(
            _note(key="peak:w2", window_start="11:00", expected_kwh=9.9, ceiling_kwh=10),
            DAY + timedelta(minutes=30),
        )
        == "persistent"
    )
    assert (
        await policy.handle(
            _note(window_start="10:00", expected_kwh=9.9, ceiling_kwh=10), DAY + timedelta(hours=1)
        )
        == "persistent"
    )


async def test_09b_a_deadline_is_said_once_until_it_clears(hass: HomeAssistant) -> None:
    """`deadline_at_risk` has no interval: once per key; a `cleared` resets it."""
    policy = _policy(hass)
    note = _note("deadline_at_risk", "deadline:ev", load="ev")
    assert await policy.handle(note, DAY) == "persistent"
    assert await policy.handle(note, DAY + timedelta(days=2)) is None
    assert (
        await policy.handle(
            _note("deadline_at_risk", "deadline:ev", load="ev", cleared=True),
            DAY + timedelta(days=2),
        )
        is None
    )
    assert "deadline:ev" not in policy.last_sent
    assert await policy.handle(note, DAY + timedelta(days=2, minutes=1)) == "persistent"


async def test_09c_quiet_hours_hold_the_ordinary_and_never_the_urgent(hass: HomeAssistant) -> None:
    """At 23:00 local a peak warning waits; a comfort violation does not."""
    policy = _policy(hass, quiet=QuietHours(time(22, 0), time(7, 0)))
    local_23 = datetime(
        2026,
        1,
        15,
        23,
        0,
        tzinfo=hass.config.time_zone and __import__("zoneinfo").ZoneInfo(hass.config.time_zone),
    )
    quiet_at = local_23.astimezone(UTC)
    assert (
        await policy.handle(_note(window_start="x", expected_kwh=1, ceiling_kwh=1), quiet_at)
        is None
    )
    assert (
        await policy.handle(_note("comfort_violation", "comfort:loop", load="loop"), quiet_at)
        == "persistent"
    )
    # And outside the quiet hours the same warning goes out.
    noon = local_23.replace(hour=12).astimezone(UTC)
    assert (
        await policy.handle(_note(window_start="x", expected_kwh=1, ceiling_kwh=1), noon)
        == "persistent"
    )


async def test_09d_a_persistent_notification_reuses_its_id(hass: HomeAssistant) -> None:
    """Two sends of one key past the interval are one notification, replaced."""
    policy = _policy(hass)
    await policy.handle(_note(window_start="10:00", expected_kwh=9.9, ceiling_kwh=10), DAY)
    await policy.handle(
        _note(window_start="10:00", expected_kwh=9.95, ceiling_kwh=10), DAY + timedelta(hours=2)
    )
    ids = _persistent_ids(hass)
    assert ids == {"powerplan_e1_peak:w1"}
    await policy.handle(
        _note(window_start="10:00", expected_kwh=9.95, ceiling_kwh=10, cleared=True),
        DAY + timedelta(hours=3),
    )
    assert _persistent_ids(hass) == set()


async def test_09e_a_missing_notify_service_falls_back_to_persistent(hass: HomeAssistant) -> None:
    """`notify.mobile_app_phone` is not there: persistent, and the site is told."""
    missing: list[str] = []
    policy = _policy(hass, on_missing_service=missing.append)
    note = _note("device_unhealthy", "unhealthy:ev", load="ev", failures=3, last_error="timeout")
    assert await policy.handle(note, DAY) == "persistent"
    assert missing == ["mobile_app_phone"]
    assert "powerplan_e1_unhealthy:ev" in _persistent_ids(hass)


async def test_09f_a_present_notify_service_is_called_with_a_tag(hass: HomeAssistant) -> None:
    """The notify transport carries title, message and a stable tag."""
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("notify", "mobile_app_phone", handler)
    policy = _policy(hass)
    note = _note("device_unhealthy", "unhealthy:ev", load="ev", failures=3, last_error="timeout")
    assert await policy.handle(note, DAY) == "notify"
    assert len(calls) == 1
    assert calls[0].data["data"]["tag"] == "powerplan_e1_unhealthy:ev"
    assert "ev" in calls[0].data["message"]


async def test_09g_off_and_unknown_categories_send_nothing(hass: HomeAssistant) -> None:
    """`level_up` is off here; an alias maps the engine's `level_step` onto it."""
    policy = _policy(hass)
    assert (
        await policy.handle(_note("level_step", "level_step:5", **{"from": "a", "to": "b"}), DAY)
        is None
    )
    assert await policy.handle(_note("something_new", "x"), DAY) is None
    assert policy.sent == []


def test_09h_the_texts_render_in_both_languages_and_tolerate_a_missing_placeholder() -> None:
    """`nb` is real bokmål; an unknown placeholder stays visible rather than raising."""
    title_en, body_en = render(
        "peak_warning", {"window_start": "17:00", "expected_kwh": 9.9, "ceiling_kwh": 10.0}, "en"
    )
    title_nb, _body_nb = render(
        "peak_warning", {"window_start": "17:00", "expected_kwh": 9.9, "ceiling_kwh": 10.0}, "nb"
    )
    assert "9.90" in body_en
    assert title_en != title_nb
    _title, body = render("device_unhealthy", {"load": "ev"}, "en")
    assert "{failures}" in body


@pytest.mark.parametrize(
    ("data", "start", "end"),
    [
        ({"start": "22:00:00", "end": "07:00:00"}, time(22, 0), time(7, 0)),
        (["23:30", "06:00"], time(23, 30), time(6, 0)),
    ],
)
def test_09i_quiet_hours_read_the_flows_shapes(data: Any, start: time, end: time) -> None:
    """A mapping or a pair, wrapping midnight."""
    quiet = QuietHours.from_data(data)
    assert quiet is not None
    assert (quiet.start, quiet.end) == (start, end)
    assert quiet.covers(datetime(2026, 1, 15, 23, 45, tzinfo=UTC))
    assert not quiet.covers(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    assert QuietHours.from_data(None) is None
