"""Greece (D13 §9.1): 6 %; 4 % on the islands of the 30 % reduction since 2026-01-01.

TEDB's code is `EL` and it still lists five islands; the list widened on 2026-01-01
(ot.gr). A slot before that date on those islands is priced at 6 %.
"""

from datetime import date
from decimal import Decimal

from .base import TEDB, CountryModule, Rate, Zone, tedb
from .registry import register

ISLANDS = (
    "https://www.ot.gr/2026/01/01/forologia/forologia-eidiseis/"
    "fpa-ta-nisia-pou-isxyoun-meiomenoi-syntelestes-apo-simera-1i-ianouariou/"
)

MODULE = register(
    CountryModule(
        code="GR",
        name="Greece",
        currency="EUR",
        vat=tedb("6"),
        tedb="EL",
        zones=(
            Zone(
                "islands",
                "North Aegean islands, Samothrace, Dodecanese islands up to 20 000 "
                "inhabitants, Lesbos, Kos, Samos, Chios",
                vat=(
                    Rate(None, Decimal("0.06"), TEDB),
                    Rate(date(2026, 1, 1), Decimal("0.04"), ISLANDS),
                ),
            ),
        ),
    )
)
