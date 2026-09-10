"""Romania (D13 §9.1): household electricity VAT 21 %, from TEDB; the state's per-kWh lines.

The cogeneration contribution, the green certificates and the excise are the
state's, per kWh excl. VAT, as ANRE's comparator states them for every zone
(D-0603). The green certificates are each supplier's pass-through of
one national quota and vary by a few tenths of a percent between suppliers; the
module holds the figure most offers state. `anre.disagreements` is the canary:
the adapter logs where the comparator has moved on from the module.
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate, tedb
from .registry import register

COMPARATOR = "https://posf.ro/comparator"
READ = date(2026, 9, 24)

MODULE = register(
    CountryModule(
        code="RO",
        name="Romania",
        currency="RON",
        time_zones=("Europe/Bucharest",),
        vat=tedb("21"),
        tedb="RO",
        levies=(
            Levy("cogenerare", (Rate(None, Decimal("0.01450"), COMPARATOR, READ),)),
            Levy("certificate_verzi", (Rate(None, Decimal("0.07402"), COMPARATOR, READ),)),
            Levy("acciza", (Rate(None, Decimal("0.00768"), COMPARATOR, READ),)),
        ),
    )
)
