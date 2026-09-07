"""Estonia (D13 §9.1): household electricity VAT 24 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="EE",
        name="Estonia",
        currency="EUR",
        time_zones=("Europe/Tallinn",),
        vat=tedb("24"),
        tedb="EE",
    )
)
