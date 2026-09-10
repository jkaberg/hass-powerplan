"""Poland (D13 §9.1): household electricity VAT 23 %, from TEDB; OZE and cogeneration.

The OZE fee and the cogeneration fee are national, per kWh excl. VAT, collected
on the grid bill: 2026's 7.30 and 3.00 PLN/MWh, as Tauron's card states them
with VAT (0.009 and 0.0037 PLN/kWh ÷ 1.23, D-0606).
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate, tedb
from .registry import register

TAURON = "https://taniej.tauron-dystrybucja.pl/"

MODULE = register(
    CountryModule(
        code="PL",
        name="Poland",
        currency="PLN",
        time_zones=("Europe/Warsaw",),
        vat=tedb("23"),
        tedb="PL",
        levies=(
            Levy("oze", (Rate(date(2026, 1, 1), Decimal("0.0073"), TAURON),)),
            Levy("kog", (Rate(date(2026, 1, 1), Decimal("0.0030"), TAURON),)),
        ),
    )
)
