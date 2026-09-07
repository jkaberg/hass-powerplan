"""Switzerland (D13 §9.1): 8.1 % since 2024-01-01 (ESTV)."""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Rate
from .registry import register

ESTV = "https://www.estv.admin.ch/estv/de/home/mehrwertsteuer/mwst-steuersaetze.html"

MODULE = register(
    CountryModule(
        code="CH",
        name="Switzerland",
        currency="CHF",
        time_zones=("Europe/Zurich",),
        vat=(Rate(date(2024, 1, 1), Decimal("0.081"), ESTV),),
    )
)
