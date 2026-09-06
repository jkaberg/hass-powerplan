"""D11 - accounting: what it cost, and what it would have cost (HLD §6.11).

Observation only. This package turns "what each load used, when, at what price"
into money the household can read, and "what it would have cost without
powerplan" into savings it can trust - with a confidence, never a guess dressed
as a fact.

It is **downstream of every decision and upstream of none** (INV-68): nothing
under `core/strategies`, `core/allocation`, `core/loads` or in `writegate.py`
imports it, and `close_slot` runs in D7's planning loop, never in the tick. A
savings number that could reach a grant would be an incentive loop - the
controller would be measured on a figure it could move.

What it owns:

* the **ledger** (`ledger.py`) - per load and per site, per calendar month in the
  site's local zone, with thirteen closed months and a lifetime;
* **pricing** (`pricing.py`) - a closed slot priced by the composed import curve
  of the load's carrier, the export credit at site level, and the month's share of
  D2's capacity fee. `Decimal`, with the currency carried, and nothing clamped
  (INV-51);
* **savings** (`savings.py`) - counterfactual less actual, energy per load and
  energy plus capacity for the site, with the calibration that gates the
  confidence;
* the **counterfactual** (`shadow/`) - one shadow store per load, the load's own
  physics under the policy its uncontrolled thermostat or charger would follow,
  with the load's own effective parameters (INV-69);
* **slot close** (`close.py`) - the one entry point, `Accounting.close_slot`.

Importing `shadow` registers the shadows that ship (D11 §3).
"""

from .close import (
    Accounting,
    AccountingConfig,
    AccountingReport,
    AccountingState,
    AccountingStatus,
    CloseCtx,
    ClosedSlot,
    LoadFigures,
    SiteFigures,
)
from .ledger import (
    KEEP_MONTHS,
    Ledger,
    Lifetime,
    LoadMonthRec,
    MonthClosed,
    SavingsConfidence,
    SiteMonthRec,
    SlotConfidence,
    month_key,
    month_start_utc,
)
from .pricing import (
    CurvePair,
    PricedSlot,
    Repriced,
    SlotPrice,
    capacity_fee_to_date,
    export_credit,
    price_slot,
    reprice,
    slot_price,
)
from .savings import (
    CALIBRATION_THRESHOLD,
    MIN_OBSERVE_DAYS,
    CalibDay,
    CalibrationRec,
    calibration_error,
    kwh_shifted,
    savings_by_party,
    savings_confidence,
    site_savings,
    site_savings_confidence,
)
from .shadow import (
    COUNTED_MODES,
    LoadParams,
    Shadow,
    ShadowCtx,
    ShadowState,
    StoreKind,
    shadow_for,
)

__all__ = [
    "CALIBRATION_THRESHOLD",
    "COUNTED_MODES",
    "KEEP_MONTHS",
    "MIN_OBSERVE_DAYS",
    "Accounting",
    "AccountingConfig",
    "AccountingReport",
    "AccountingState",
    "AccountingStatus",
    "CalibDay",
    "CalibrationRec",
    "CloseCtx",
    "ClosedSlot",
    "CurvePair",
    "Ledger",
    "Lifetime",
    "LoadFigures",
    "LoadMonthRec",
    "LoadParams",
    "MonthClosed",
    "PricedSlot",
    "Repriced",
    "SavingsConfidence",
    "Shadow",
    "ShadowCtx",
    "ShadowState",
    "SiteFigures",
    "SiteMonthRec",
    "SlotConfidence",
    "SlotPrice",
    "StoreKind",
    "calibration_error",
    "capacity_fee_to_date",
    "export_credit",
    "kwh_shifted",
    "month_key",
    "month_start_utc",
    "price_slot",
    "reprice",
    "savings_by_party",
    "savings_confidence",
    "shadow_for",
    "site_savings",
    "site_savings_confidence",
    "slot_price",
]
