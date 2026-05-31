"""The repairs catalogue and the fix flows (D8 §5.9, §8).

Every issue id powerplan raises is a row here with its severity and whether
it is fixable; the runtime and the engine report conditions by id and this
module creates or clears the registry issue under a per-site id
(`{entry_id}_{issue_id}`). `RepairsWatch` evaluates the conditions the runtime
can see on its own - a stale meter, a silent register, a dead price source, a
recovered store - edge-triggered after every tick.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .core.model import Snapshot

__all__ = [
    "CATALOGUE",
    "Issue",
    "RepairsWatch",
    "async_clear",
    "async_create_fix_flow",
    "async_report",
    "registry_id",
]


@dataclass(frozen=True, slots=True)
class Issue:
    """One row of D8 §5.9."""

    severity: ir.IssueSeverity
    fixable: bool = False
    #: Whether the issue survives a restart (HA persists issues unless told otherwise).
    persistent: bool = True


CATALOGUE: dict[str, Issue] = {
    "meter_stale": Issue(ir.IssueSeverity.WARNING),
    "register_missing": Issue(ir.IssueSeverity.WARNING),
    "scaling_mismatch": Issue(ir.IssueSeverity.WARNING),
    "price_source_dead": Issue(ir.IssueSeverity.ERROR),
    "preset_outdated": Issue(ir.IssueSeverity.WARNING),
    "bound_helper_missing": Issue(ir.IssueSeverity.WARNING, fixable=True),
    "role_missing": Issue(ir.IssueSeverity.ERROR, fixable=True),
    "provision_refused": Issue(ir.IssueSeverity.WARNING),
    "legionella_at_risk": Issue(ir.IssueSeverity.WARNING),
    "engine_failing": Issue(ir.IssueSeverity.ERROR, fixable=True, persistent=False),
    "store_reset": Issue(ir.IssueSeverity.WARNING),
    "delegated_idle": Issue(ir.IssueSeverity.WARNING),
    "savings_low_confidence": Issue(ir.IssueSeverity.WARNING),
    "load_error": Issue(ir.IssueSeverity.WARNING),
    "notify_service_missing": Issue(ir.IssueSeverity.WARNING),
}

#: D8 §5.9: the meter's power must be stale this long before the issue is raised.
METER_STALE_AFTER = timedelta(minutes=10)
#: D8 §5.9: no register report for this long.
REGISTER_MISSING_AFTER = timedelta(hours=24)
#: D8 §5.9: the integral's bias against the register, and for how many windows.
SCALING_BIAS_FRACTION = 0.05
SCALING_WINDOWS = 6
#: D11 §5.5: a load's calibration error over threshold this long before it is worth telling.
SAVINGS_LOW_CONFIDENCE_AFTER = timedelta(days=7)


def registry_id(entry_id: str, issue_id: str) -> str:
    """Return the issue registry id of one site's issue; the catalogue key is its prefix."""
    return f"{entry_id}_{issue_id}"


def catalogue_key(issue_id: str) -> str:
    """Return the catalogue row an issue id belongs to (`load_error_<load>` → `load_error`)."""
    for key in sorted(CATALOGUE, key=len, reverse=True):
        if issue_id == key or issue_id.startswith(f"{key}_"):
            return key
    return issue_id


def async_report(
    hass: HomeAssistant,
    entry_id: str,
    issue_id: str,
    *,
    active: bool,
    placeholders: Mapping[str, Any] | None = None,
    entry_title: str | None = None,
) -> None:
    """Create or clear one issue by its catalogue row (D8 §5.9)."""
    key = catalogue_key(issue_id)
    row = CATALOGUE.get(key, Issue(ir.IssueSeverity.WARNING))
    if not active:
        async_clear(hass, entry_id, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        registry_id(entry_id, issue_id),
        is_fixable=row.fixable,
        is_persistent=row.persistent,
        severity=row.severity,
        translation_key=key,
        translation_placeholders={
            "site": entry_title or entry_id,
            **{name: str(value) for name, value in (placeholders or {}).items()},
        },
        data={"entry_id": entry_id, "issue_id": issue_id},
    )


def async_clear(hass: HomeAssistant, entry_id: str, issue_id: str) -> None:
    """Remove one site's issue if it exists."""
    ir.async_delete_issue(hass, DOMAIN, registry_id(entry_id, issue_id))


# --------------------------------------------------------------------------- #
# Conditions the runtime sees on its own
# --------------------------------------------------------------------------- #


@dataclass
class RepairsWatch:
    """Raise and clear the runtime-detected rows of §5.9, edge-triggered per tick."""

    #: The site's `Runtime`; typed loosely because `runtime.py` imports this module.
    runtime: Any
    bias_windows: int = 0
    windows_seen: int = 0
    active: set[str] = field(default_factory=set)
    #: D11 §5.5, per load: since when its `savings_confidence` has read `low`,
    #: unbroken; popped the moment it reads anything else (INV-63: calibration
    #: never changes a parameter, and this issue never either).
    low_since: dict[str, datetime] = field(default_factory=dict)

    def evaluate(self, now: datetime, snapshot: Snapshot) -> None:
        """Compare the conditions to what is raised and change only what changed."""
        conditions = {
            "meter_stale": self._meter_stale(now, snapshot),
            "register_missing": self._register_missing(now, snapshot),
            "scaling_mismatch": self._scaling_mismatch(snapshot),
            "price_source_dead": bool(self.runtime.dead_sources),
            "store_reset": self.runtime.store.corrupt_path is not None,
            "preset_outdated": self.runtime.build.preset_outdated,
        }
        placeholders: dict[str, dict[str, Any]] = {
            "price_source_dead": {"source": ", ".join(sorted(self.runtime.dead_sources))},
            "store_reset": {"path": self.runtime.store.corrupt_path or ""},
            "preset_outdated": {"preset": str(self.runtime.build.preset_file or "")},
        }
        for load_id, wanted in self._savings_low_confidence(now, snapshot).items():
            issue_id = f"savings_low_confidence_{load_id}"
            conditions[issue_id] = wanted
            load = snapshot.loads.get(load_id)
            placeholders[issue_id] = {"load": load.name if load is not None else load_id}
        for issue_id, wanted in conditions.items():
            if wanted == (issue_id in self.active):
                continue
            async_report(
                self.runtime.hass,
                self.runtime.entry.entry_id,
                issue_id,
                active=wanted,
                placeholders=placeholders.get(issue_id),
                entry_title=self.runtime.site_name,
            )
            if wanted:
                self.active.add(issue_id)
            else:
                self.active.discard(issue_id)

    def _meter_stale(self, now: datetime, snapshot: Snapshot) -> bool:
        """D8 §5.9: the power reading is older than ten minutes."""
        del now
        meter = snapshot.meter
        if meter is None or not snapshot.health.stale_meter:
            return False
        age = meter.health.power_age_s
        return age is not None and age >= METER_STALE_AFTER.total_seconds()

    def _register_missing(self, now: datetime, snapshot: Snapshot) -> bool:
        meter = snapshot.meter
        if meter is None or not self.runtime.has_register:
            return False
        age = meter.health.register_age_s
        if age is None:
            started = self.runtime.started_at
            return started is not None and now - started >= REGISTER_MISSING_AFTER
        return age >= REGISTER_MISSING_AFTER.total_seconds()

    def _scaling_mismatch(self, snapshot: Snapshot) -> bool:
        meter = snapshot.meter
        if meter is None:
            return False
        closed = len(meter.closed)
        if closed <= self.windows_seen:
            return self.bias_windows >= SCALING_WINDOWS
        self.windows_seen = closed
        bias = meter.health.integral_bias_w
        mean = meter.grid_smooth_w if meter.grid_smooth_w is not None else meter.grid_w
        if bias is None or not mean:
            self.bias_windows = 0
        elif abs(bias) > SCALING_BIAS_FRACTION * abs(mean):
            self.bias_windows += 1
        else:
            self.bias_windows = 0
        return self.bias_windows >= SCALING_WINDOWS

    def _savings_low_confidence(self, now: datetime, snapshot: Snapshot) -> dict[str, bool]:
        """D11 §5.5: a load's `savings_confidence` reading `low` for seven days, unbroken."""
        wanted: dict[str, bool] = {}
        seen = set()
        for load_id, row in snapshot.accounting.per_load.items():
            seen.add(load_id)
            if not isinstance(row, Mapping) or row.get("savings_confidence") != "low":
                self.low_since.pop(load_id, None)
                wanted[load_id] = False
                continue
            since = self.low_since.setdefault(load_id, now)
            wanted[load_id] = now - since >= SAVINGS_LOW_CONFIDENCE_AFTER
        for load_id in list(self.low_since):
            if load_id not in seen:
                self.low_since.pop(load_id, None)
        return wanted


# --------------------------------------------------------------------------- #
# Fix flows
# --------------------------------------------------------------------------- #


class AcknowledgeSafeModeFlow(RepairsFlow):
    """`engine_failing`: acknowledging leaves safe mode and resumes control (D7 §8)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Remember which site to release."""
        self._hass = hass
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """Show the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> Any:
        """On confirmation, acknowledge; the runtime clears the issue itself."""
        if user_input is not None:
            entry = self._hass.config_entries.async_get_entry(self._entry_id)
            runtime = getattr(entry, "runtime_data", None) if entry is not None else None
            if runtime is not None:
                await runtime.async_acknowledge_safe_mode()
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Return the fix flow for a fixable issue (Home Assistant calls this by name)."""
    entry_id = str((data or {}).get("entry_id", ""))
    key = catalogue_key(str((data or {}).get("issue_id", issue_id)))
    if key == "engine_failing":
        return AcknowledgeSafeModeFlow(hass, entry_id)
    return ConfirmRepairFlow()
