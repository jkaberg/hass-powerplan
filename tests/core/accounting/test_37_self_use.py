"""D11 §9 37 - a battery with its own self-use is compared with that self-use.

Without powerplan a hybrid inverter still charges from what the house would
export and discharges into what it would import. Comparing it with an idle
battery books that as powerplan's saving (PLAN dec. 44, D11 §5.9.1). The
self-use shadow replays the inverter alone from the site's measured flows; a
day on which powerplan did exactly that saves nothing, and a day it also moved
energy in time saves only that.
"""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import LoadParams, StoreKind
from custom_components.powerplan.core.loads.stores.energy import EnergyStore
from tests.core.accounting.conftest import ORDINARY, closed_slot, curve, local, shadow_ctx, site

STORE = EnergyStore(
    capacity_kwh=10.0,
    min_soc=21.0,
    max_soc=100.0,
    max_charge_w=5000.0,
    usable_fraction=0.95,
    charge_eff=0.95,
    discharge_eff=0.95,
    reserve_soc=20.0,
    max_discharge_w=5000.0,
)


def _site(flows: dict[int, tuple[float, float, float]]):
    """Run a day: per hour `(import, export, battery)` kWh, signed by INV-19."""
    under_test = site(import_curve=curve(ORDINARY, days=1))
    under_test.with_load(
        "battery",
        shadow_ctx(
            params=LoadParams(kind=StoreKind.BATTERY_SELF_USE, nameplate_w=5000.0, store=STORE),
            level_now=50.0,
        ),
    )
    for hour in range(24):
        imported, exported, battery = flows.get(hour, (0.0, 0.0, 0.0))
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                import_kwh=imported,
                export_kwh=exported,
                loads={"battery": battery},
            )
        )
    return under_test


@pytest.mark.inv("INV-69")
def test_37_doing_what_the_inverter_does_alone_saves_nothing() -> None:
    """Noon: 5 kWh the house would export, stored; evening: 4 kWh it would import, delivered."""
    under_test = _site({12: (0.0, 0.0, 5.0), 18: (0.0, 0.0, -4.0)})

    rec = under_test.accounting.state().ledger.loads["battery"]
    assert rec.cf_kwh == pytest.approx(1.0), "the shadow stored 5 kWh and gave back 4"
    assert rec.savings.amount == rec.cf_cost.amount - rec.cost.amount
    assert rec.savings.amount == pytest.approx(Decimal(0), abs=Decimal("0.001"))


def test_37_moving_energy_in_time_is_credited_only_for_what_self_use_could_not_do() -> None:
    """A night grid charge for the evening: self-use alone would have given 2.7 kWh of it."""
    # 02:00: 5 kWh from the grid into the battery (the house's own net is 0);
    # 17:00: 4.5 kWh out of it into what the house would have imported.
    under_test = _site({2: (5.0, 0.0, 5.0), 17: (0.0, 0.0, -4.5)})

    rec = under_test.accounting.state().ledger.loads["battery"]
    # The shadow never grid-charges; at 17:00 it gives what it held above the
    # reserve: (50 − 20) % × 0.095 kWh/% × 0.95 = 2.7075 kWh.
    assert rec.cf_kwh == pytest.approx(-2.7075)
    assert rec.savings.amount == rec.cf_cost.amount - rec.cost.amount
    idle_savings = -rec.cost.amount
    assert Decimal(0) < rec.savings.amount < idle_savings, "less than the idle battery claimed"
