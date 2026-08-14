"""The site's selects (D8 §5.5): presence, the capacity target, the risk.

Each restores its last option on start and pushes it to the runtime, which
reads it live on the next tick (INV-47). The target's options come from the
tariff's own step table (D2 §6), so a select never offers a step the tariff has
not got.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .core.tariffs.grammar import StepTable
from .entity import PowerplanEntity
from .load_entities import load_selects
from .runtime import step_index

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .runtime import Runtime

PRESENCE_OPTIONS: tuple[str, ...] = ("auto", "home", "away", "vacation")
RISK_OPTIONS: tuple[str, ...] = ("flat", "free_ride", "full")

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the three selects."""
    runtime = entry.runtime_data
    async_add_entities([PresenceSelect(runtime), TargetSelect(runtime), RiskSelect(runtime)])
    runtime.setup_load_platform(async_add_entities, load_selects)


class _RestoringSelect(PowerplanEntity, SelectEntity, RestoreEntity):
    """A select whose last option is pushed back into the runtime on start."""

    async def async_added_to_hass(self) -> None:
        """Restore the last option (INV-47)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        restored = None if last is None else self._restored(last.state)
        if (
            restored is not None
            and restored in (self.options or ())
            and restored != self.current_option
        ):
            await self.async_select_option(restored)

    def _restored(self, state: str) -> str:
        """Return the option a restored state means; an old spelling reads as the new one."""
        return state


class PresenceSelect(_RestoringSelect):
    """`select.<site>_presence`: auto (from the `person` entities) or a manual mode."""

    _attr_icon = "mdi:home-account"

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "presence")
        self._attr_options = list(PRESENCE_OPTIONS)

    @property
    def current_option(self) -> str:
        """The setting, not the derived presence."""
        return self.runtime.presence_setting

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The presence in force, which `auto` derives."""
        snapshot = self.snapshot
        return {
            "effective": None
            if snapshot is None or snapshot.site.presence is None
            else snapshot.site.presence.value
        }

    async def async_select_option(self, option: str) -> None:
        """Set the presence setting."""
        await self.runtime.async_set_presence_setting(option)
        self.async_write_ha_state()


class TargetSelect(_RestoringSelect):
    """`select.<site>_target`: automatic, a step of the tariff, or the configured kW."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:target"

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site; the options are the tariff's."""
        super().__init__(runtime, "target")
        self._attr_options = list(runtime.build.target_options)

    @property
    def current_option(self) -> str:
        """The target in force."""
        return self.runtime.target_choice

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the choice means: its kW, and a step's range and fee (review ENT-2).

        A state translation takes no placeholders (D8 §5.15 H1), so the state
        reads "Trinn 2" and the numbers a household would want beside it are
        attributes here.
        """
        snapshot = self.snapshot
        attributes: dict[str, Any] = {
            "target_kw": None if snapshot is None else snapshot.site.target_kw
        }
        index = step_index(self.current_option)
        peak = self.runtime.build.tariff.active_version().peak
        if index is not None and peak is not None and isinstance(peak.pricing, StepTable):
            steps = peak.pricing.steps
            if index < len(steps):
                fee = steps[index].fee_per_period
                attributes.update(
                    {
                        "lower_kw": 0.0 if index == 0 else peak.pricing.upper_kw(index - 1),
                        "upper_kw": steps[index].upper_kw,
                        # Two decimals, not the ledger's working precision.
                        "fee": f"{fee.amount:.2f}",
                        "currency": fee.currency,
                    }
                )
        return attributes

    def _restored(self, state: str) -> str:
        """Read a `step:<i>` saved before WP U.1 as the option `step_<i>` (D8 §9 22)."""
        index = step_index(state)
        return state if index is None else f"step_{index}"

    async def async_select_option(self, option: str) -> None:
        """Set the ceiling's target."""
        await self.runtime.async_set_target_choice(option)
        self.async_write_ha_state()


class RiskSelect(_RestoringSelect):
    """`select.<site>_risk`: never exceed / today's paid hours / period average (D2 §6)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:dice-multiple"

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "risk")
        self._attr_options = list(RISK_OPTIONS)

    @property
    def current_option(self) -> str:
        """The risk label in force."""
        return self.runtime.risk_choice

    async def async_select_option(self, option: str) -> None:
        """Set the risk."""
        await self.runtime.async_set_risk_choice(option)
        self.async_write_ha_state()
