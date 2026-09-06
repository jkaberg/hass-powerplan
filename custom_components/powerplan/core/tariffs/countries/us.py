"""The United States (D13 §9.1): no VAT; state and local taxes vary, so the flow asks."""

from .base import CountryModule
from .registry import register

MODULE = register(CountryModule(code="US", name="United States", currency="USD", vat=()))
