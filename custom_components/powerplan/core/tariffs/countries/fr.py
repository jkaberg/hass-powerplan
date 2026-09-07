"""France (D13 §9.1): 20 %, the subscription included since 2025-08-01; the DOM by postcode."""

from decimal import Decimal

from .base import CountryModule, Rate, Zone, tedb
from .registry import register

DOM = "https://bofip.impots.gouv.fr/bofip/343-PGP.html/identifiant=BOI-TVA-GEO-20-10-20190605"

MODULE = register(
    CountryModule(
        code="FR",
        name="France",
        currency="EUR",
        time_zones=(
            "Europe/Paris",
            "America/Guadeloupe",
            "America/Martinique",
            "Indian/Reunion",
            "America/Cayenne",
            "Indian/Mayotte",
        ),
        vat=tedb("20"),
        tedb="FR",
        zones=(
            Zone(
                "gpmr", "Guadeloupe, Martinique, Réunion", vat=(Rate(None, Decimal("0.021"), DOM),)
            ),
            Zone("guyane_mayotte", "Guyane, Mayotte", vat=(Rate(None, Decimal(0), DOM),)),
        ),
    )
)
