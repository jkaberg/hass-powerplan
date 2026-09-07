"""Romania (D13 §9.1): household electricity VAT 21 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="RO",
        name="Romania",
        currency="RON",
        time_zones=("Europe/Bucharest",),
        vat=tedb("21"),
        tedb="RO",
    )
)
