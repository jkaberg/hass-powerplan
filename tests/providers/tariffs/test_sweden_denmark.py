"""Sweden's and Denmark's fetchers on their captured documents (D13 §5.5, §5.9; INV-75).

Eltariff (T1a) before Ei's file (T6) in Sweden; elpris.dk (T1a) in Denmark with
Datahub's next season. No socket opens; every document is the captured one.
"""

from __future__ import annotations

from datetime import date

from custom_components.powerplan.core.tariffs.sources import Tier
from custom_components.powerplan.providers.tariffs import base, ladder
from custom_components.powerplan.providers.tariffs.ei_household import EiHousehold
from custom_components.powerplan.providers.tariffs.elpris_dk import ElprisDk
from custom_components.powerplan.providers.tariffs.eltariff import Eltariff
from tests.builders.tariff_sources import denmark_http, sweden_http


def test_swedens_ladder_is_eltariff_then_eis_file() -> None:
    """INV-75: the regulator's file only where the companies' API has nothing."""
    base.register(Eltariff)
    base.register(EiHousehold)
    assert base.for_country("SE") == [Eltariff, EiHousehold]
    assert (Eltariff.tier, EiHousehold.tier) == (Tier.T1A, Tier.T6)


async def test_eltariff_lists_the_companies_whose_endpoint_answered() -> None:
    """Norrtälje's endpoint answered with a redirect: left out, the rest listed, both zones."""
    operators = await Eltariff().operators(sweden_http())  # type: ignore[arg-type]
    names = [operator.name for operator in operators]
    assert "Göteborg Energi Nät AB" in names
    assert "Norrtälje Energi AB" not in names
    assert all(operator.zones == ("", "norr") for operator in operators)


async def test_a_company_eltariff_does_not_serve_comes_from_eis_file() -> None:
    """Vattenfall publishes no Eltariff endpoint: the ladder falls to Ei's file."""
    base.register(Eltariff)
    base.register(EiHousehold)
    http = sweden_http()
    resolved = await ladder.resolve(
        http,  # type: ignore[arg-type]
        "SE",
        "Vattenfall Eldistribution AB",
        "Lokalnät Syd|villa20",
        answers={"no_power_fee": True},
    )
    assert resolved.source is EiHousehold
    assert resolved.fetched.grid.provenance.tier == "T6"
    assert resolved.fetched.grid.fixed_fee[-1].valid_from == date(2026, 1, 1)


async def test_goteborg_comes_from_eltariff() -> None:
    """The same company in both: the API's copy, never the file's."""
    base.register(Eltariff)
    base.register(EiHousehold)
    resolved = await ladder.resolve(
        sweden_http(),  # type: ignore[arg-type]
        "SE",
        "Göteborg Energi Nät AB",
        "GN10KW",
    )
    assert resolved.source is Eltariff


async def test_denmark_takes_elpris_and_datahubs_next_season() -> None:
    """Radius: the summer table from elpris.dk, the winter one from Datahub."""
    base.register(ElprisDk)
    http = denmark_http()
    source = ElprisDk()
    operators = await source.operators(http)  # type: ignore[arg-type]
    assert "Radius Elnet A/S" in [operator.name for operator in operators]
    fetched = await source.fetch(http, "791", None, {})  # type: ignore[arg-type]
    assert [v.valid_from for v in fetched.grid.energy] == [date(2026, 4, 1), date(2026, 10, 1)]


async def test_denmark_without_datahub_keeps_the_tariff_in_force() -> None:
    """Datahub down: the copy has elpris.dk's season and ends with it."""
    http = denmark_http()
    for url in [url for url in http.documents if "energidataservice" in url]:
        del http.documents[url]
    fetched = await ElprisDk().fetch(http, "791", None, {})  # type: ignore[arg-type]
    assert [v.valid_from for v in fetched.grid.energy] == [date(2026, 4, 1)]
    assert fetched.grid.valid_to == date(2026, 9, 30)
