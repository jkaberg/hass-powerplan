"""Finland (D13 §9.1): household electricity VAT 25.5 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="FI",
        name="Finland",
        currency="EUR",
        time_zones=("Europe/Helsinki",),
        vat=tedb("25.5"),
        tedb="FI",
        postcode="sahkonhinta",
    )
)
