"""The Netherlands (D13 §9.1): 21 %, from TEDB; the connection rule template."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="NL",
        name="Netherlands",
        currency="EUR",
        vat=tedb("21"),
        tedb="NL",
        rule_template="nl/connection",
    )
)
