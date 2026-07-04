"""D10's one call from `plan()` (mirrors `core/accounting_hook.py`).

`ForecastsAdapter.close_slot` is called for every price slot the engine
closes. A window closes far less often than a slot - an hourly Tensio
window under quarter-hour prices closes on one slot in four - so this is
also where the controlled loads' own energy is summed across the slots
between one window close and the next, the same `SlotClose` →
`ClosedWindow` relationship `core/accounting_hook.py` already accumulates
across for D11's own ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .engine import ForecastClose
from .forecasts.baseline import HourOfWeekBaseline
from .forecasts.model import OFFER_CONFIDENCE
from .state_codec import encode

if TYPE_CHECKING:
    from .engine import SlotClose

#: The key `state_codec.encode(BaselineState)` is stored under inside
#: `ForecastClose.state`/`EngineState.forecasts` - one key today, room for a
#: second (a weather series cache) without a schema bump (D10 §7).
BASELINE_STATE_KEY = "baseline"

__all__ = ["ForecastsAdapter"]


@dataclass
class ForecastsAdapter:
    """Feeds D10's baseline from the engine's own slot closes (D10 §5.1)."""

    baseline: HourOfWeekBaseline
    #: `load_id -> kWh` accumulated since the last window closed. A load added
    #: mid-window has no `slot` for the ticks it missed and contributes
    #: nothing for them - the same "biased high, and said so" discipline
    #: `reconstruct.py` documents for the seeded history.
    _accumulated: dict[str, float] = field(default_factory=dict)
    _was_ready: bool = False

    def close_slot(self, close: SlotClose) -> ForecastClose:
        """Fold one slot's controlled energy in; update the baseline on a window close."""
        for load_id, row in close.loads.items():
            if row.slot is None:
                continue
            self._accumulated[load_id] = self._accumulated.get(load_id, 0.0) + row.slot.kwh
        window = close.window_closed
        if window is None:
            return ForecastClose()
        controlled_kwh = sum(self._accumulated.values())
        self._accumulated.clear()
        uncontrolled_kwh = window.kwh - controlled_kwh
        self.baseline.update(window, uncontrolled_kwh, close.outdoor_c)
        # The bin this update just fed, not `close.end` (window_min minutes
        # later, always the *next* hour-of-week bin for a 60-min window) -
        # `baseline_ready` answers "did this update cross the gate", which
        # only the bin it actually touched can say.
        confidence = self.baseline.confidence(window.start_utc)
        ready = confidence >= OFFER_CONFIDENCE and not self._was_ready
        if confidence >= OFFER_CONFIDENCE:
            self._was_ready = True
        return ForecastClose(
            window_updated=True,
            baseline_ready=ready,
            confidence=confidence,
            state={BASELINE_STATE_KEY: encode(self.baseline.state)},
        )
