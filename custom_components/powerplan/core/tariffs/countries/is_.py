"""Iceland (D13 §9.1): 24 %; 11 % on a home's heating meter is a per-load tariff."""

from decimal import Decimal

from .base import CountryModule, Rate
from .registry import register

SKATTURINN = (
    "https://www.skatturinn.is/atvinnurekstur/virdisaukaskattur/skattskylda-og-skattprosentur/"
)

MODULE = register(
    CountryModule(
        code="IS",
        name="Iceland",
        currency="ISK",
        time_zones=("Atlantic/Reykjavik",),
        vat=(Rate(None, Decimal("0.24"), SKATTURINN),),
    )
)
