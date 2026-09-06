"""Slovakia (D13 §9.1): household electricity VAT 19 %, the reduced rate on electricity, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(code="SK", name="Slovakia", currency="EUR", vat=tedb("19"), tedb="SK")
)
