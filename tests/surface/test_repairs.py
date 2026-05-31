"""D8 §9 10: issues appear on their condition, go when it clears, and the fixable one fixes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan import repairs
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import AccountingStatus, Engine, EngineHealth
from tests.runtime.conftest import SITE_ENTRY_ID, site_data, site_entry

if TYPE_CHECKING:
    import pytest
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter


def _write_corrupt(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json {", encoding="utf-8")


def _issue(hass: HomeAssistant, entry_id: str, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, repairs.registry_id(entry_id, issue_id))


def test_10_the_catalogue_is_d8s_table() -> None:
    """Every id of §5.9 with its severity and fixability."""
    assert repairs.CATALOGUE["engine_failing"].fixable
    assert repairs.CATALOGUE["engine_failing"].severity is ir.IssueSeverity.ERROR
    assert repairs.CATALOGUE["price_source_dead"].severity is ir.IssueSeverity.ERROR
    assert not repairs.CATALOGUE["meter_stale"].fixable
    assert repairs.catalogue_key("load_error_ev") == "load_error"
    assert repairs.catalogue_key("meter_stale") == "meter_stale"


async def test_10b_a_stale_meter_raises_after_ten_minutes_and_clears_on_a_reading(
    hass: HomeAssistant,
    site: MockConfigEntry,
    runtime: Runtime,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
) -> None:
    """`meter_stale`: the power reading stops; ten minutes later the issue; a reading clears it."""
    freezer.tick(timedelta(minutes=4))
    await runtime.run_tick("heartbeat")
    assert runtime.snapshot is not None
    assert runtime.snapshot.health.stale_meter, "four minutes without a reading is stale (D3)"
    assert _issue(hass, site.entry_id, "meter_stale") is None, "not ten minutes yet"

    freezer.tick(timedelta(minutes=7))
    await runtime.run_tick("heartbeat")
    assert _issue(hass, site.entry_id, "meter_stale") is not None

    meter.set_power(1_500.0)
    await runtime.run_tick("power")
    assert _issue(hass, site.entry_id, "meter_stale") is None


async def test_10c_a_dead_price_source_is_an_error_issue(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """`price_source_dead` follows the runtime's own bookkeeping, edge-triggered."""
    runtime.dead_sources = {"manual"}
    await runtime.run_tick("heartbeat")
    issue = _issue(hass, site.entry_id, "price_source_dead")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["source"] == "manual"
    runtime.dead_sources = set()
    await runtime.run_tick("heartbeat")
    assert _issue(hass, site.entry_id, "price_source_dead") is None


async def test_10d_an_outdated_preset_is_reported(hass: HomeAssistant, meter: FakeMeter) -> None:
    """A site set up with other version ids than the shipped preset's gets `preset_outdated`."""
    data = site_data(hass)
    data["tariff"]["version_ids"] = ["2025-01-01"]
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: PLC0415

    entry = MockConfigEntry(domain=DOMAIN, title="Old site", entry_id="OLDSITE", data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry.entry_id, "preset_outdated") is not None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_10e_a_recovered_store_is_reported(
    hass: HomeAssistant, meter: FakeMeter, hass_config_dir: str
) -> None:
    """`store_reset`: a corrupt store file was quarantined at load."""
    path = Path(hass.config.path(".storage", f"{DOMAIN}.{SITE_ENTRY_ID}"))
    await hass.async_add_executor_job(_write_corrupt, path)
    from custom_components.powerplan.storage import SiteStore  # noqa: PLC0415

    entry = site_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime: Runtime = entry.runtime_data
    assert isinstance(runtime.store, SiteStore)
    assert runtime.store.corrupt_path is not None
    assert _issue(hass, entry.entry_id, "store_reset") is not None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_10f_the_engine_failing_fix_flow_leaves_safe_mode(
    hass: HomeAssistant,
    site: MockConfigEntry,
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three failures raise the fixable issue; confirming the flow acknowledges and clears it."""

    def boom(self: Engine, state: Any, inputs: Any, started: float) -> Any:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(Engine, "_run", boom)
    for _ in range(3):
        await runtime.run_tick("heartbeat")
    monkeypatch.undo()
    issue = _issue(hass, site.entry_id, "engine_failing")
    assert issue is not None
    assert issue.is_fixable
    assert runtime.state.runtime.safe_mode

    flow = await repairs.async_create_fix_flow(
        hass, issue.issue_id, {"entry_id": site.entry_id, "issue_id": "engine_failing"}
    )
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == "form"
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert not runtime.state.runtime.safe_mode
    assert _issue(hass, site.entry_id, "engine_failing") is None
    assert runtime.snapshot is not None
    assert runtime.snapshot.health.engine is EngineHealth.OK


async def test_10g_a_loads_low_confidence_for_a_week_raises_and_recovery_clears(
    hass: HomeAssistant,
    site: MockConfigEntry,
    runtime: Runtime,
    meter: FakeMeter,
    freezer: FrozenDateTimeFactory,
) -> None:
    """D11 §5.5: seven unbroken days of `savings_confidence: low`, named by the load."""
    await runtime.run_tick("test")
    assert runtime.snapshot is not None
    low = replace(
        runtime.snapshot,
        accounting=AccountingStatus(per_load={"ev": {"savings_confidence": "low"}}),
    )
    now = runtime.state.runtime.last_tick_at or freezer.time_to_freeze
    watch = runtime.repairs

    watch.evaluate(now, low)
    assert _issue(hass, site.entry_id, "savings_low_confidence_ev") is None, "not a week yet"

    watch.evaluate(now + timedelta(days=6, hours=23), low)
    assert _issue(hass, site.entry_id, "savings_low_confidence_ev") is None

    watch.evaluate(now + timedelta(days=7, hours=1), low)
    issue = _issue(hass, site.entry_id, "savings_low_confidence_ev")
    assert issue is not None
    assert issue.translation_placeholders is not None
    # No load "ev" in this bare snapshot's `loads`, so the name falls back to the id.
    assert issue.translation_placeholders.get("load") == "ev"

    # Recovery clears it, and the clock resets - a later relapse waits its own week.
    ok = replace(
        runtime.snapshot,
        accounting=AccountingStatus(per_load={"ev": {"savings_confidence": "ok"}}),
    )
    watch.evaluate(now + timedelta(days=7, hours=2), ok)
    assert _issue(hass, site.entry_id, "savings_low_confidence_ev") is None
    assert "ev" not in watch.low_since
