"""Luxembourg (D13 §9.1): household electricity VAT 8 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="LU",
        name="Luxembourg",
        currency="EUR",
        time_zones=("Europe/Luxembourg",),
        vat=tedb("8"),
        tedb="LU",
    )
)
