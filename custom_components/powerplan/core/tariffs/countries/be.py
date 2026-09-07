"""Belgium (D13 §9.1): household electricity VAT 6 % on a residential contract, from TEDB (Royal Decree 20, table A, XIV)."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="BE",
        name="Belgium",
        currency="EUR",
        time_zones=("Europe/Brussels",),
        vat=tedb("6"),
        tedb="BE",
    )
)
