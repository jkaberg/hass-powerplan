"""Malta (D13 §9.1): 5 % (VAT Act, Eighth Schedule). TEDB has 18 % only."""

from decimal import Decimal

from .base import CountryModule, Rate
from .registry import register

MTCA = "https://mtca.gov.mt/docs/default-source/documents/business-tax/vat/faqs/vat-rates-exemptions---faqs.pdf"

MODULE = register(
    CountryModule(
        code="MT", name="Malta", currency="EUR", vat=(Rate(None, Decimal("0.05"), MTCA),), tedb="MT"
    )
)
