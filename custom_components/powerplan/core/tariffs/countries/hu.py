"""Hungary (D13 §9.1): household electricity VAT 27 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(code="HU", name="Hungary", currency="HUF", vat=tedb("27"), tedb="HU")
)
