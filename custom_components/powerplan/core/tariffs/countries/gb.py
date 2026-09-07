"""The United Kingdom (D13 §9.1): 5 % on domestic power; Great Britain 0 % for six months.

Great Britain zero-rates domestic electricity from 2026-10-01 to 2027-03-31
(Revenue and Customs Brief 10 (2026)); Northern Ireland stays at 5 % - the BT
postcode settles it. HA's code is `GB`; the rule template is `uk/nopeak`.
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Rate, Zone
from .registry import register

NOTICE = "https://www.gov.uk/guidance/vat-on-fuel-and-power-notice-70119"
BRIEF = (
    "https://www.gov.uk/government/publications/revenue-and-customs-brief-10-2026-temporary-"
    "zero-rate-of-vat-for-domestic-electricity-in-great-britain/temporary-zero-rate-of-vat-"
    "for-domestic-electricity-in-great-britain"
)

MODULE = register(
    CountryModule(
        code="GB",
        name="United Kingdom",
        currency="GBP",
        time_zones=("Europe/London", "Europe/Belfast"),
        vat=(
            Rate(None, Decimal("0.05"), NOTICE),
            Rate(date(2026, 10, 1), Decimal(0), BRIEF),
            Rate(date(2027, 4, 1), Decimal("0.05"), BRIEF),
        ),
        zones=(
            Zone(
                "northern_ireland", "Northern Ireland", vat=(Rate(None, Decimal("0.05"), NOTICE),)
            ),
        ),
        rule_template="uk/nopeak",
    )
)
