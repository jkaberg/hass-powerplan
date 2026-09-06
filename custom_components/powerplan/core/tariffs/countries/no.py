"""Norway (D13 §9, §9.1): 25 % VAT, forbruksavgift and the Enova levy, three zones.

Households in Nordland, Troms and Finnmark pay no VAT on electricity (mval. § 6-6);
in the tiltakssone - Finnmark and seven Troms municipalities - they pay no
forbruksavgift either (the Storting's decision, § 3 a). The zone comes from the
postcode's municipality (D13 §6 step 0), else NVE per grid company, else asked.

Forbruksavgift moved three times in 2025 and once for 2026 (Lovdata, the Storting's
yearly decisions); the Enova levy is 1 øre/kWh for households (forskrift om
Energifondet § 3). Every levy here is excl. VAT, as the law states it.
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate, Zone
from .registry import register

MVA = "https://www.skatteetaten.no/satser/merverdiavgift/"
MVAL_6_6 = "https://lovdata.no/dokument/NL/lov/2009-06-19-58"
ELAVGIFT_2025 = "https://lovdata.no/forskrift/2024-12-13-3216"
ELAVGIFT_2026 = "https://lovdata.no/dokument/STV/forskrift/2025-12-18-2760"
ENOVA = "https://lovdata.no/dokument/SF/forskrift/2001-12-10-1377"

FORBRUKSAVGIFT = Levy(
    "forbruksavgift",
    (
        Rate(date(2025, 1, 1), Decimal("0.0979"), ELAVGIFT_2025),
        Rate(date(2025, 4, 1), Decimal("0.1693"), ELAVGIFT_2025),
        Rate(date(2025, 10, 1), Decimal("0.1253"), ELAVGIFT_2025),
        Rate(date(2026, 1, 1), Decimal("0.0713"), ELAVGIFT_2026),
    ),
)
ENOVA_LEVY = Levy("enova", (Rate(None, Decimal("0.01"), ENOVA),))
NO_VAT = (Rate(None, Decimal(0), MVAL_6_6),)

MODULE = register(
    CountryModule(
        code="NO",
        name="Norway",
        currency="NOK",
        vat=(Rate(None, Decimal("0.25"), MVA),),
        levies=(FORBRUKSAVGIFT, ENOVA_LEVY),
        zones=(
            Zone("nord", "Nordland; Troms outside the tiltakssone", vat=NO_VAT),
            Zone(
                "tiltakssone",
                "Finnmark; Karlsøy, Kvænangen, Kåfjord, Lyngen, Nordreisa, Skjervøy, Storfjord",
                vat=NO_VAT,
                levies=(
                    Levy(
                        "forbruksavgift",
                        (
                            Rate(date(2025, 1, 1), Decimal(0), ELAVGIFT_2025),
                            Rate(date(2026, 1, 1), Decimal(0), ELAVGIFT_2026),
                        ),
                    ),
                ),
            ),
        ),
        rule_template="no/template",
    )
)
