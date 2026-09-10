"""Portugal (D13 §9.1): 23 %; Azores 16 %, Madeira 22 % - by postcode.

The 6 % band on the first 200 kWh per 30 days (300 for five or more), for a
contracted power up to 6.9 kVA (D13 §18 G17), read per month-to-date as
D1 §5.4 says; its regional counterparts are the Azores' 4 % and Madeira's 4 %
since 2024-10-01 (Decreto Legislativo Regional 6/2024/M, art. 21 - was 5 %).
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Rate, VatBand, Zone, tedb
from .registry import register

ERSE = "https://www.erse.pt/media/tcsfm4n2/ersexplica_iva-fatura_2025.pdf"
MADEIRA = "https://joram.madeira.gov.pt/joram/1serie/Ano%20de%202024/ISerie-105-2024-06-21sup.pdf"

MODULE = register(
    CountryModule(
        code="PT",
        name="Portugal",
        currency="EUR",
        time_zones=("Europe/Lisbon", "Atlantic/Azores", "Atlantic/Madeira"),
        vat=tedb("23"),
        tedb="PT",
        rule_template="pt/contracted",
        zones=(
            Zone("azores", "Azores", vat=(Rate(None, Decimal("0.16"), ERSE),)),
            Zone("madeira", "Madeira", vat=(Rate(None, Decimal("0.22"), ERSE),)),
        ),
        vat_band=VatBand(
            upto_kwh=200.0,
            large_upto_kwh=300.0,
            upto_kw=6.9,
            rates=(Rate(None, Decimal("0.06"), ERSE),),
            zones=(
                ("azores", (Rate(None, Decimal("0.04"), ERSE),)),
                (
                    "madeira",
                    (
                        Rate(None, Decimal("0.05"), ERSE),
                        Rate(date(2024, 10, 1), Decimal("0.04"), MADEIRA),
                    ),
                ),
            ),
        ),
    )
)
