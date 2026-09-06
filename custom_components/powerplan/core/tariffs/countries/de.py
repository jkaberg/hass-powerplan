"""Germany (D13 §9.1): 19 %; Heligoland no VAT, Büsingen Swiss VAT - by postcode."""

from decimal import Decimal

from .base import CountryModule, Rate, Zone, tedb
from .registry import register

USTG = "https://www.gesetze-im-internet.de/ustg_1980/__1.html"
BUSINGEN = (
    "https://www.buesingen.de/de/Unser-Buesingen/Deutsche-Insel-in-der-Schweiz/Steuerregelung"
)

MODULE = register(
    CountryModule(
        code="DE",
        name="Germany",
        currency="EUR",
        vat=tedb("19"),
        tedb="DE",
        zones=(
            Zone("heligoland", "Heligoland", vat=(Rate(None, Decimal(0), USTG),)),
            Zone(
                "busingen", "Büsingen am Hochrhein", vat=(Rate(None, Decimal("0.081"), BUSINGEN),)
            ),
        ),
    )
)
