"""Denmark (D13 §9, §9.1): household electricity VAT 25 %, from TEDB, and elafgift.

Elafgift is 72.0 øre/kWh excl. VAT in 2025 and 0.8 øre - the EU minimum - from
2026-01-01 to 2027-12-31 (Skatteministeriet; elpris.dk's `nationalCharges.json`
states 0.008 DKK as `EA-001`). The rate from 2028 is not yet
written: `until` makes `tools/preset_age.py` warn as the end nears. Energinet's
system and transmission tariffs are a company's prices and come with the copy
(`elpris_dk`, INV-70).
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate, tedb
from .registry import register

ELAFGIFT = (
    "https://skat.dk/erhverv/afgifter-paa-varer-og-ydelser-punktafgifter/nyhedsbrev-afgifter/"
    "midlertidig-nedsaettelse-af-elafgiften-i-2026-og-2027"
)

MODULE = register(
    CountryModule(
        code="DK",
        name="Denmark",
        currency="DKK",
        time_zones=("Europe/Copenhagen",),
        vat=tedb("25"),
        tedb="DK",
        levies=(
            Levy(
                "elafgift",
                (
                    Rate(date(2025, 1, 1), Decimal("0.720"), ELAFGIFT),
                    Rate(date(2026, 1, 1), Decimal("0.008"), ELAFGIFT, until=date(2027, 12, 31)),
                ),
            ),
        ),
    )
)
