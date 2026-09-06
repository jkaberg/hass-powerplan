"""Italy (D13 §9.1): household electricity VAT 10 % for household use, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(CountryModule(code="IT", name="Italy", currency="EUR", vat=tedb("10"), tedb="IT"))
