"""Sweden (D13 §9, §9.1): 25 % VAT and energiskatt, reduced in the north.

Energiskatt 2026 is 36.0 öre/kWh excl. VAT; households in every municipality of
Norrbotten, Västerbotten and Jämtland (and listed ones in four more counties) pay
9.6 öre less (Skatteverket). Which municipality a house is in comes from the
Eltariff product or the postcode; until then the national rate applies.
"""

from datetime import date
from decimal import Decimal

from .base import CountryModule, Levy, Rate, Zone, tedb
from .registry import register

ENERGISKATT = (
    "https://www.skatteverket.se/foretag/skatterochavdrag/punktskatter/energiskatter/"
    "skattpael.4.15532c7b1442f256bae5e4c.html"
)

MODULE = register(
    CountryModule(
        code="SE",
        name="Sweden",
        currency="SEK",
        vat=tedb("25"),
        tedb="SE",
        levies=(Levy("energiskatt", (Rate(date(2026, 1, 1), Decimal("0.360"), ENERGISKATT),)),),
        zones=(
            Zone(
                "norr",
                "Norrbotten, Västerbotten, Jämtland and the listed municipalities of "
                "Västernorrland, Gävleborg, Dalarna and Värmland",
                levies=(
                    Levy("energiskatt", (Rate(date(2026, 1, 1), Decimal("0.264"), ENERGISKATT),)),
                ),
            ),
        ),
    )
)
