"""The United States (D13 §9.1): no VAT; state and local taxes vary, so the flow asks."""

from .base import CountryModule
from .registry import register

MODULE = register(
    CountryModule(
        code="US",
        name="United States",
        currency="USD",
        time_zones=(
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Phoenix",
            "America/Los_Angeles",
            "America/Anchorage",
            "Pacific/Honolulu",
            "America/Detroit",
            "America/Boise",
            "America/Indiana/Indianapolis",
            "America/Kentucky/Louisville",
        ),
        vat=(),
        postcode="openei",
    )
)
