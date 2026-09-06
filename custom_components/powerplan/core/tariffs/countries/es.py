"""Spain (D13 §9.1): 21 %; 10 % for ≤ 10 kW from 2026-08-01 to 09-30; the Canaries' IGIC.

The temporary 10 % follows RDL 18/2026's trigger on the electricity CPI and reads
the contracted kW the tariff already holds. The Canary Islands levy IGIC, not VAT:
0 % for a home of ≤ 10 kW, 3 % otherwise (Ley 4/2012, art. 51). Ceuta and Melilla
(IPSI) are not read yet.
"""

from datetime import date
from decimal import Decimal

from .base import TEDB, CountryModule, Rate, Zone
from .registry import register

IGIC = "https://www.boe.es/buscar/act.php?id=BOE-A-2012-9899"

MODULE = register(
    CountryModule(
        code="ES",
        name="Spain",
        currency="EUR",
        vat=(
            Rate(None, Decimal("0.21"), TEDB),
            Rate(date(2026, 8, 1), Decimal("0.10"), TEDB, upto_kw=10.0),
            Rate(date(2026, 10, 1), Decimal("0.21"), TEDB),
        ),
        tedb="ES",
        zones=(
            Zone(
                "canarias",
                "Canary Islands",
                vat=(
                    Rate(None, Decimal("0.03"), IGIC),
                    Rate(None, Decimal(0), IGIC, upto_kw=10.0),
                ),
            ),
        ),
        rule_template="es/2_0td",
    )
)
