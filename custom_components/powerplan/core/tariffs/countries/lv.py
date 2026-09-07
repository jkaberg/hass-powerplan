"""Latvia (D13 §9.1): household electricity VAT 21 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="LV",
        name="Latvia",
        currency="EUR",
        time_zones=("Europe/Riga",),
        vat=tedb("21"),
        tedb="LV",
    )
)
