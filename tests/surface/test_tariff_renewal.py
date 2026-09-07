"""The tariff copy's renewal: monthly, on request, never at start (D13 §10, D7 §5.9).

D13 §19 5 and 14; D7 §9 24, 25; D8 §9 35, 36. A fake source stands in for the
grid company's (D9 §5.15): it is registered under the key the copy names, so the
runtime renews from it exactly as it would from a real adapter, and sockets stay
closed throughout - any real fetch would fail the test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.powerplan import repairs
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.tariffs import household
from custom_components.powerplan.core.tariffs.sources import Tier
from custom_components.powerplan.providers.tariffs import base
from custom_components.powerplan.storage import migrate_tariff
from tests.builders.tariff_sources import fake, tensio_grid
from tests.runtime.conftest import site_data

if TYPE_CHECKING:
    from collections.abc import Iterator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import Event, HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
KEY = "fakenett"


@pytest.fixture
def frozen(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock on the day of the renewal tests."""
    freezer.move_to(NOW)
    return NOW


def _copy(hass: HomeAssistant, **grid: Any) -> dict[str, Any]:
    """Return a site's data whose copy came from the fake, with the first two versions only."""
    data, _ = migrate_tariff(site_data(hass), NOW.date())
    price = household.from_json(data["tariff"]["price"])
    fetched = tensio_grid(KEY, Tier.T1B)
    copy = replace(
        fetched,
        capacity=fetched.capacity[:2],
        energy=fetched.energy[:2],
        provenance=replace(fetched.provenance, fetched=date(2026, 9, 1)),
        operator_key="tensio-ts",
        **{"renew_at": date(2026, 10, 1), **grid},
    )
    data["tariff"]["price"] = household.to_json(replace(price, grid=copy))
    return data


async def _site(hass: HomeAssistant, data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Renew", entry_id="RENEW", data=data, version=1, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def source() -> Iterator[type]:
    """Register the fake under the copy's source key, and take it away again."""
    cls = fake(KEY, Tier.T1B)
    base.register(cls)
    yield cls
    base.unregister(KEY)


def _updates(hass: HomeAssistant) -> list[Event]:
    seen: list[Event] = []
    hass.bus.async_listen(f"{DOMAIN}_tariff_updated", seen.append)
    return seen


@pytest.mark.inv("INV-73")
async def test_14_no_fetch_at_start_and_the_timer_fires_on_renew_at(
    hass: HomeAssistant,
    frozen: datetime,
    meter: FakeMeter,
    source: type,
    freezer: FrozenDateTimeFactory,
) -> None:
    """D7 §9 24: the setup fetches nothing; the timer fires on `renew_at`, at 03:17 local."""
    entry = await _site(hass, _copy(hass))
    assert source.calls == [], "no tariff fetch at start (INV-73)"
    runtime: Runtime = entry.runtime_data
    due = datetime(2026, 10, 1, 3, 17, tzinfo=runtime.build.cfg.tz).astimezone(UTC)

    freezer.move_to(due - timedelta(minutes=1))
    async_fire_time_changed(hass, due - timedelta(minutes=1))
    await hass.async_block_till_done()
    assert source.calls == []

    freezer.move_to(due + timedelta(seconds=1))
    async_fire_time_changed(hass, due + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert source.calls == ["tensio-ts"]
    price = household.from_json(entry.data["tariff"]["price"])
    assert len(price.grid.capacity) == 3
    assert price.grid.provenance.fetched == date(2026, 10, 1)
    assert price.grid.renew_at == date(2026, 11, 1), "a month after the last fetch"
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-73")
async def test_14_an_overdue_copy_still_waits_an_hour_after_start(
    hass: HomeAssistant,
    frozen: datetime,
    meter: FakeMeter,
    source: type,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A copy whose `renew_at` passed while HA was down is not fetched at start (INV-73)."""
    entry = await _site(hass, _copy(hass, renew_at=date(2026, 9, 1)))
    async_fire_time_changed(hass, NOW + timedelta(minutes=30))
    await hass.async_block_till_done()
    assert source.calls == []
    freezer.move_to(NOW + timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass, NOW + timedelta(hours=1, minutes=1))
    await hass.async_block_till_done()
    assert source.calls == ["tensio-ts"]
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-52")
async def test_05_refresh_tariff_appends_answers_and_fires(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter, source: type
) -> None:
    """D8 §9 35: `refresh_tariff` answers what it did; `tariff_updated` fires on a change."""
    entry = await _site(hass, _copy(hass))
    runtime: Runtime = entry.runtime_data
    events = _updates(hass)

    # D7 §9 25: the renewal runs outside the tick lock - held here, it still completes.
    async with runtime.lock:
        answer = await hass.services.async_call(
            DOMAIN, "refresh_tariff", {"site": entry.entry_id}, blocking=True, return_response=True
        )
    await hass.async_block_till_done()

    assert answer == {
        "sites": {
            entry.entry_id: {
                "source": KEY,
                "fetched": "2026-09-24",
                "added": ["2026-07-01"],
                "changed": [],
                "kept": ["2025-07-01", "2026-01-01"],
                "next_renewal": "2026-10-24",
            }
        }
    }
    assert [event.data["added"] for event in events] == [["2026-07-01"]]
    # D7 §9 25: written without a reload, the evaluator swapped in place.
    assert entry.runtime_data is runtime
    assert [v.valid_from for v in runtime.build.tariff.spec.versions][-1] == date(2026, 7, 1)
    assert len(household.from_json(entry.data["tariff"]["price"]).grid.capacity) == 3

    again = await hass.services.async_call(
        DOMAIN, "refresh_tariff", {}, blocking=True, return_response=True
    )
    await hass.async_block_till_done()
    assert again["sites"][entry.entry_id]["added"] == []  # type: ignore[index]
    assert len(events) == 1, "no event when nothing changed"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_05_a_failed_refresh_changes_nothing(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter
) -> None:
    """The source is down: `tariff_refresh_failed`, and the copy is exactly as it was."""
    base.register(fake(KEY, Tier.T1B, behaviour="down"))
    try:
        entry = await _site(hass, _copy(hass))
        before = dict(entry.data)
        with pytest.raises(HomeAssistantError) as raised:
            await hass.services.async_call(
                DOMAIN, "refresh_tariff", {}, blocking=True, return_response=True
            )
        assert raised.value.translation_key == "tariff_refresh_failed"
        assert raised.value.translation_placeholders["source"] == KEY
        assert dict(entry.data) == before
        await hass.config_entries.async_unload(entry.entry_id)
    finally:
        base.unregister(KEY)


async def test_05_a_template_has_nothing_to_fetch(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter
) -> None:
    """A copy no source fetched answers with its own kind and changes nothing (§10)."""
    data, _ = migrate_tariff(site_data(hass), NOW.date())
    entry = await _site(hass, data)
    answer = await hass.services.async_call(
        DOMAIN, "refresh_tariff", {}, blocking=True, return_response=True
    )
    site = answer["sites"][entry.entry_id]  # type: ignore[index]
    assert (site["source"], site["added"], site["next_renewal"]) == ("shipped", [], None)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_36_a_renewal_that_disagrees_with_the_household_keeps_its_answer(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter
) -> None:
    """The household confirmed 60-minute windows; the source now says 15: kept, reviewed."""
    base.register(fake(KEY, Tier.T1B, stated={"window_min": 15}))
    try:
        data = _copy(hass)
        data["tariff"]["price"]["confirmed"] = {"window_min": 60}
        entry = await _site(hass, data)
        await hass.services.async_call(
            DOMAIN, "refresh_tariff", {}, blocking=True, return_response=True
        )
        await hass.async_block_till_done()
        assert entry.data["tariff"]["review"] == ["window_min"]
        assert household.from_json(entry.data["tariff"]["price"]).confirmed == {"window_min": 60}
        runtime: Runtime = entry.runtime_data
        runtime.repairs.evaluate(NOW, runtime.snapshot)  # type: ignore[arg-type]
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, repairs.registry_id(entry.entry_id, "tariff_review")
        )
        assert issue is not None
        assert issue.is_fixable
        runtime.confirm_tariff_review()
        assert entry.data["tariff"]["review"] == []
        await hass.config_entries.async_unload(entry.entry_id)
    finally:
        base.unregister(KEY)


async def test_36_tariff_stale_after_the_last_version_ends_with_the_renewal_failing(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """The copy ended 2026-09-20; renewals fail: `tariff_stale` is raised."""
    base.register(fake(KEY, Tier.T1B, behaviour="down"))
    try:
        entry = await _site(
            hass, _copy(hass, valid_to=date(2026, 9, 20), renew_at=date(2026, 9, 1))
        )
        runtime: Runtime = entry.runtime_data
        assert not runtime.tariff_stale(NOW.date())
        later = NOW + timedelta(hours=1, minutes=1)
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
        assert runtime.tariff_stale(later.date())
        runtime.repairs.evaluate(later, runtime.snapshot)  # type: ignore[arg-type]
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, repairs.registry_id(entry.entry_id, "tariff_stale")
        )
        assert issue is not None
        await hass.config_entries.async_unload(entry.entry_id)
    finally:
        base.unregister(KEY)


async def test_22_a_fetched_copy_credits_its_source(
    hass: HomeAssistant, frozen: datetime, meter: FakeMeter
) -> None:
    """The copy came from a source whose licence asks for credit: named, linked, licensed."""
    from custom_components.powerplan.core.tariffs.sources import Credit  # noqa: PLC0415
    from custom_components.powerplan.sensor import tariff_credit  # noqa: PLC0415

    credit = Credit("Fri Nettleie", "https://github.com/kraftsystemet/fri-nettleie", "CC BY 4.0")
    base.register(fake(KEY, Tier.T1B, licence="CC BY 4.0", credit=credit))
    try:
        entry = await _site(hass, _copy(hass))
        assert tariff_credit(entry.runtime_data) == [
            {
                "name": "Fri Nettleie",
                "url": "https://github.com/kraftsystemet/fri-nettleie",
                "licence": "CC BY 4.0",
            }
        ]
        await hass.config_entries.async_unload(entry.entry_id)
    finally:
        base.unregister(KEY)
