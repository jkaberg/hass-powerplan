"""Portugal (D13 §9.1): 23 %; Azores 16 %, Madeira 22 % - by postcode.

The 6 % band on the first 200 kWh per 30 days (300 for five or more) is D13 §18
G17, TS.7; its regional counterparts are the Azores' 4 % and Madeira's 4 % since
2024-10-01 (Decreto Legislativo Regional 6/2024/M, art. 21 - was 5 %).
"""

from decimal import Decimal

from .base import CountryModule, Rate, Zone, tedb
from .registry import register

ERSE = "https://www.erse.pt/media/tcsfm4n2/ersexplica_iva-fatura_2025.pdf"

MODULE = register(
    CountryModule(
        code="PT",
        name="Portugal",
        currency="EUR",
        vat=tedb("23"),
        tedb="PT",
        zones=(
            Zone("azores", "Azores", vat=(Rate(None, Decimal("0.16"), ERSE),)),
            Zone("madeira", "Madeira", vat=(Rate(None, Decimal("0.22"), ERSE),)),
        ),
    )
)
