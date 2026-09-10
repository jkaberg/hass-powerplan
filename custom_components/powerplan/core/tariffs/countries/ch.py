"""Switzerland (D13 §9.1): 8.1 % since 2024-01-01 (ESTV); the federal grid surcharge.

The Netzzuschlag (EnG art. 35) is 2.3 Rp./kWh since 2018-01-01, the same
everywhere - ElCom's `aidfee` for every municipality and category.
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate
from .registry import register

ESTV = "https://www.estv.admin.ch/estv/de/home/mehrwertsteuer/mwst-steuersaetze.html"
ENG = "https://www.fedlex.admin.ch/eli/cc/2017/762/de#art_35"

MODULE = register(
    CountryModule(
        code="CH",
        name="Switzerland",
        currency="CHF",
        time_zones=("Europe/Zurich",),
        postcode="elcom",
        vat=(Rate(date(2024, 1, 1), Decimal("0.081"), ESTV),),
        levies=(Levy("netzzuschlag", (Rate(date(2018, 1, 1), Decimal("0.023"), ENG),)),),
    )
)
