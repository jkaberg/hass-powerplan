"""Czechia (D13 §9.1): household electricity VAT 21 %, from TEDB."""

from .base import CountryModule, tedb
from .registry import register

MODULE = register(
    CountryModule(code="CZ", name="Czechia", currency="CZK", vat=tedb("21"), tedb="CZ")
)
