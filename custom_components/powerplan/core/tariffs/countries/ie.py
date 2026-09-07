"""Ireland (D13 §9.1): 9 % until 2030-12-31 (Budget 2026). TEDB has 23 % only."""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Rate
from .registry import register

BUDGET = "https://www.revenue.ie/en/corporate/press-office/budget-information/current-year/budget-summary.pdf"

MODULE = register(
    CountryModule(
        code="IE",
        name="Ireland",
        currency="EUR",
        time_zones=("Europe/Dublin",),
        vat=(Rate(None, Decimal("0.09"), BUDGET, until=date(2030, 12, 31)),),
        tedb="IE",
    )
)
