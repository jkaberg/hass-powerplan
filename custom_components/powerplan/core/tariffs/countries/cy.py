"""Cyprus (D13 §9.1): 9 % on household electricity until 2027-03-31, then 19 %.

TEDB has 19 % only; the Cabinet extended the reduced rate.
"""

from datetime import date
from decimal import Decimal

from .base import TEDB, CountryModule, Rate
from .registry import register

CABINET = (
    "https://cyprus-mail.com/2026/02/04/reduced-vat-on-electricity-bills-extended-for-another-year"
)

MODULE = register(
    CountryModule(
        code="CY",
        name="Cyprus",
        currency="EUR",
        time_zones=("Asia/Nicosia", "Asia/Famagusta"),
        vat=(
            Rate(None, Decimal("0.09"), CABINET),
            Rate(date(2027, 4, 1), Decimal("0.19"), TEDB),
        ),
        tedb="CY",
    )
)
