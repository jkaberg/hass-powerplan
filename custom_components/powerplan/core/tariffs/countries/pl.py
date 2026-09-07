"""Poland (D13 §9.1): household electricity VAT 23 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="PL",
        name="Poland",
        currency="PLN",
        time_zones=("Europe/Warsaw",),
        vat=tedb("23"),
        tedb="PL",
    )
)
