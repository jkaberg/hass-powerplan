"""Croatia (D13 §9.1): household electricity VAT 13 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(code="HR", name="Croatia", currency="EUR", vat=tedb("13"), tedb="HR")
)
