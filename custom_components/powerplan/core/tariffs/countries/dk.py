"""Denmark (D13 §9.1): household electricity VAT 25 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="DK",
        name="Denmark",
        currency="DKK",
        time_zones=("Europe/Copenhagen",),
        vat=tedb("25"),
        tedb="DK",
    )
)
