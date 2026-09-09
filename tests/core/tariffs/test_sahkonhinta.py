"""Finland's postcode directory: sahkonhinta.fi's grid companies (D13 §5.9)."""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.tariffs.sources import QualityError, sahkonhinta
from tests.builders.tariff_sources import FIXTURES

DSOS = (FIXTURES / "sahkonhinta" / "getdsocollection.json").read_bytes()


def test_a_postcode_names_its_grid_company() -> None:
    """00100 Helsinki lists Helen Sähköverkko among its companies; an unknown postcode none."""
    assert "Helen Sähköverkko Oy" in sahkonhinta.place_of("00100", DSOS).grid_companies
    assert sahkonhinta.place_of("00001", DSOS).grid_companies == ()


def test_a_list_that_is_not_the_sites_is_refused() -> None:
    """Anything but the regulator's list fails the quality check."""
    with pytest.raises(QualityError):
        sahkonhinta.place_of("00100", b"<html>")
