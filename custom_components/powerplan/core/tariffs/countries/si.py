"""Slovenia (D13 §9.1): household electricity VAT 22 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="SI",
        name="Slovenia",
        currency="EUR",
        time_zones=("Europe/Ljubljana",),
        vat=tedb("22"),
        tedb="SI",
    )
)
