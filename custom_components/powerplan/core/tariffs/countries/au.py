"""Australia (D13 §9.1): GST 10 % (GST Act s 9-70); a CDR plan states whether it is included."""

from decimal import Decimal

from .base import CountryModule, Rate
from .registry import register

GST = "https://www5.austlii.edu.au/au/legis/cth/consol_act/antsasta1999402/s9.70.html"

MODULE = register(
    CountryModule(
        code="AU", name="Australia", currency="AUD", vat=(Rate(None, Decimal("0.10"), GST),)
    )
)
