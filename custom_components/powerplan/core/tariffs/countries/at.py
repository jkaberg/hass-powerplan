"""Austria (D13 §9.1): 20 %; Jungholz and Mittelberg 19 % - settled by the postcode."""

from .base import CountryModule, Zone, tedb
from .registry import register

MODULE = register(
    CountryModule(
        code="AT",
        name="Austria",
        currency="EUR",
        vat=tedb("20"),
        tedb="AT",
        zones=(Zone("jungholz_mittelberg", "Jungholz, Mittelberg", vat=tedb("19")),),
    )
)
