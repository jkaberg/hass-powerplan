"""Bulgaria (D13 §9.1): household electricity VAT 20 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(code="BG", name="Bulgaria", currency="EUR", vat=tedb("20"), tedb="BG")
)
