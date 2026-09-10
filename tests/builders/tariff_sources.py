"""Fake tariff sources - one per tier, no network (D9 §5.15, D13 §19 15).

A fake lists the operators it is told to, and answers with Tensio TS's copy, a
quality failure or an unreachable endpoint, so the flow and the ladder can be
walked exactly as a real adapter would drive them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from custom_components.powerplan.core.tariffs.household import Provenance, TaxZone, from_preset
from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Product,
    QualityError,
    Question,
    Tier,
    UnreachableError,
)
from tests.builders.presets import fixture_raw

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from custom_components.powerplan.providers.tariffs import Http
    from custom_components.powerplan.providers.tariffs.fri_nettleie import Bundle

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tariff_sources"
#: fri-nettleie's archive at commit da8a6886: `tariffer/`, Elhub's GLN list, the licence.
FRI_ARCHIVE = FIXTURES / "fri_nettleie" / "fri-nettleie-da8a6886.tar.gz"
NVE_COUNTIES = FIXTURES / "nve" / "husholdning-2026-09-01.json"
CAPTURED = date(2026, 9, 24)

TENSIO = Operator("tensio-ts", "Tensio TS", products=(Product("bolig", "Bolig"),))
ELVIA = Operator("elvia", "Elvia", zones=("", "nord"))


def tensio_grid(source: str = "fake", tier: Tier = Tier.T1B):  # type: ignore[no-untyped-def]
    """Return Tensio TS's copy as a fetched source gives it (excl. nothing: as published)."""
    grid = from_preset(fixture_raw("no/tensio-ts"), source=source, zone=TaxZone("NO")).grid
    return replace(
        grid, provenance=Provenance(source=source, url="https://example.invalid", tier=tier.value)
    )


def fake(
    key: str,
    tier: Tier,
    *,
    behaviour: str = "ok",
    operators: tuple[Operator, ...] = (TENSIO,),
    country: str = "NO",
    licence: str | None = None,
    credit: Credit | None = None,
    questions: tuple[Question, ...] = (),
    stated: Mapping[str, Any] | None = None,
) -> type:
    """Return a source class: `ok`, `quality` (fails the check) or `down` (unreachable)."""
    stated = stated or {}

    class Fake:
        calls: ClassVar[list[str]] = []

        async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
            if behaviour == "down":
                raise UnreachableError(f"{key}: HTTP 503")
            return list(operators)

        async def fetch(
            self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
        ) -> Fetched:
            type(self).calls.append(operator)
            if behaviour == "quality":
                raise QualityError(f"{key}: a basis it labels wrong")
            grid = replace(tensio_grid(key, tier), operator_key=operator, product_key=product)
            return Fetched(grid=grid, questions=questions, stated=dict(stated))

    Fake.key = key  # type: ignore[attr-defined]
    Fake.country = country  # type: ignore[attr-defined]
    Fake.tier = tier  # type: ignore[attr-defined]
    Fake.licence = licence  # type: ignore[attr-defined]
    Fake.credit = credit or Credit(f"{key} data", f"https://{key}.invalid", licence)  # type: ignore[attr-defined]
    Fake.__name__ = f"Fake_{key}"
    return Fake


@cache
def fri_bundle() -> Bundle:
    """Return the captured fri-nettleie archive, unpacked as the fetcher does."""
    from custom_components.powerplan.providers.tariffs.fri_nettleie import unpack  # noqa: PLC0415

    return unpack(FRI_ARCHIVE.read_bytes())


@dataclass
class FixtureHttp:
    """`Http` over captured documents: what it was asked, dated the capture day, no socket."""

    documents: dict[str, bytes]
    day: date = CAPTURED
    asked: list[str] = field(default_factory=list)
    released: int = 0
    parsed: dict[str, Any] = field(default_factory=dict)

    async def post(self, url: str, body: Mapping[str, Any]) -> bytes:
        """Return the captured answer to a POST, keyed as `Http.post` keys it."""
        return await self.get(f"POST {url} {json.dumps(body, sort_keys=True)}")

    async def get(self, url: str, **headers: str) -> bytes:
        """Return the captured document; a URL not captured is unreachable."""
        self.asked.append(url)
        if url not in self.documents:
            raise UnreachableError(f"{url}: not captured")
        return self.documents[url]

    def today(self) -> date:
        """Return the capture day."""
        return self.day

    async def executor(self, job: Callable[..., Any], *args: Any) -> Any:
        """Run the job inline."""
        return job(*args)

    def release(self) -> None:
        """Count the releases (D13 §5.2 rule 6)."""
        self.released += 1
        self.parsed.clear()

    async def document(self, url: str, parse: Callable[[bytes], Any]) -> Any:
        """Return `url` parsed, once until released."""
        if url not in self.parsed:
            self.parsed[url] = parse(await self.get(url))
        return self.parsed[url]


def fri_http() -> FixtureHttp:
    """Serve fri-nettleie's archive and NVE's county list as captured."""
    from custom_components.powerplan.core.tariffs.sources import fri_nettleie, nve  # noqa: PLC0415

    return FixtureHttp(
        {
            fri_nettleie.TARBALL: FRI_ARCHIVE.read_bytes(),
            nve.URL.format(day="2026-09-01"): NVE_COUNTIES.read_bytes(),
        }
    )


def _eltariff_file(api_url: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", api_url.split("//", 1)[1].lower()).strip("-")
    return FIXTURES / "eltariff" / f"{slug}.json"


#: Ei's page as far as the fetcher reads it: the workbook's link (captured).
EI_PAGE = (
    b'<a href="/download/18.6586000219eb48404e7ac7/1781185169892/'
    b'Hush%C3%A5llskunder.xlsx">Hush\xc3\xa5llskunder</a>'
)


def sweden_http() -> FixtureHttp:
    """Serve Eltariff's catalogue and endpoints and Ei's page and workbook as captured."""
    from custom_components.powerplan.core.tariffs.sources import (  # noqa: PLC0415
        ei_household,
        eltariff,
    )

    catalogue = FIXTURES / "eltariff" / "catalogue.json"
    documents = {eltariff.CATALOGUE: catalogue.read_bytes()}
    for entry in json.loads(catalogue.read_bytes()):
        path = _eltariff_file(str(entry["apiUrl"]))
        if path.is_file():
            documents[f"{str(entry['apiUrl']).rstrip('/')}/tariffs"] = path.read_bytes()
    documents[ei_household.PAGE] = EI_PAGE
    documents[ei_household.workbook_url(EI_PAGE)] = (
        FIXTURES / "ei" / "Hushallskunder.xlsx"
    ).read_bytes()
    return FixtureHttp(documents)


def denmark_http() -> FixtureHttp:
    """Serve elpris.dk's documents for areas 131, 145 and 791, and Radius's Datahub rows."""
    from custom_components.powerplan.core.tariffs.sources import (  # noqa: PLC0415
        datahub_pricelist,
        elpris_dk,
    )

    folder = FIXTURES / "elpris_dk"
    documents = {
        elpris_dk.STATIC: (folder / "static.json").read_bytes(),
        elpris_dk.NATIONAL: (folder / "nationalCharges.json").read_bytes(),
        datahub_pricelist.query("Radius Elnet A/S", "DT_C_01", CAPTURED): (
            FIXTURES / "datahub" / "radius-DT_C_01.json"
        ).read_bytes(),
        datahub_pricelist.query("Radius Elnet A/S", "DT_C_01", date(2026, 4, 1)): (
            FIXTURES / "datahub" / "radius-DT_C_01-from-2026-04-01.json"
        ).read_bytes(),
    }
    for area in ("131", "145", "791"):
        documents[elpris_dk.AREA.format(area=area)] = (
            folder / f"distributionAreaCharge_{area}.json"
        ).read_bytes()
    return FixtureHttp(documents)


def post_key(url: str, body: Mapping[str, Any]) -> str:
    """Return the key `FixtureHttp.post` looks a captured answer up by."""
    return f"POST {url} {json.dumps(body, sort_keys=True)}"


def belgium_http() -> FixtureHttp:
    """Serve VREG's page and 2026 sheet, and CompaCWaPE's and BruSim's captured answers."""
    from custom_components.powerplan.core.tariffs.sources import cwape, vreg_xlsx  # noqa: PLC0415

    sheet = FIXTURES / "vreg" / "Distributienettarieven elektriciteit 2026.xlsx"
    link = (
        "https://assets.vlaamsenutsregulator.be/2025-11/Distributienettarieven%20elektriciteit"
        "%202026.xlsx?VersionId=yIPLVXG9cytG2_k0HI0ZF_BF4Fa27nV7"
    )
    folder = FIXTURES / "cwape"
    documents = {
        vreg_xlsx.PAGE: f'<a href="{link}">2026</a>'.encode(),
        link: sheet.read_bytes(),
    }
    for key, host, _ in cwape.HOSTS:
        code = {"compacwape": "5000", "brusim": "1000"}[key]
        documents[f"{host}/postal_codes?code={code}"] = (
            folder / f"{key}-postal-{code}.json"
        ).read_bytes()
        documents[f"{host}/distribution_network_managers"] = (
            folder / f"{key}-dnms.json"
        ).read_bytes()
        documents[f"{host}/connection_power_segments"] = (
            folder / f"{key}-segments.json"
        ).read_bytes()
        segment = None if key == "compacwape" else "/connection_power_segments/3"
        for entry in json.loads((folder / f"{key}-postal-{code}.json").read_bytes()):
            for product in ("single", "dual"):
                answer = folder / f"{key}-sim-{entry['id']}-{product}.json"
                documents[
                    post_key(
                        f"{host}/offer_simulations", cwape.body(str(entry["id"]), product, segment)
                    )
                ] = answer.read_bytes()
    return FixtureHttp(documents)


def switzerland_http() -> FixtureHttp:
    """Serve ElCom's captured answers: Bern's postcode, every municipality, Bern and Basel."""
    from custom_components.powerplan.core.tariffs.sources import elcom  # noqa: PLC0415

    folder = FIXTURES / "elcom"
    empty = b'{"data":{"observations":[]}}'
    documents = {
        post_key(elcom.API, elcom.search_query("3011")): (folder / "search-3011.json").read_bytes(),
        post_key(elcom.API, elcom.municipalities_query()): (
            folder / "municipalities.json"
        ).read_bytes(),
    }
    for municipality, category in (("351", "H4"), ("2701", "H2")):
        documents[post_key(elcom.API, elcom.observations_query(municipality, category, 2026))] = (
            folder / f"observations-{municipality}-{category}-2026.json"
        ).read_bytes()
        documents[post_key(elcom.API, elcom.observations_query(municipality, category, 2027))] = (
            empty
        )
    return FixtureHttp(documents)


def romania_http() -> FixtureHttp:
    """Serve ANRE's counties and two zones' captured offers (trimmed to five each)."""
    from custom_components.powerplan.core.tariffs.sources import anre  # noqa: PLC0415

    folder = FIXTURES / "anre"
    documents = {anre.COUNTIES: (folder / "judete.json").read_bytes()}
    for zone in ("4", "7"):
        documents[anre.offers_url(zone, CAPTURED)] = (
            folder / f"comparator-electric-{zone}.json"
        ).read_bytes()
    return FixtureHttp(documents)


def slovakia_http() -> FixtureHttp:
    """Serve ZSDIS's switching-times page, trimmed to its household literal."""
    from custom_components.powerplan.core.tariffs.sources import zsdis  # noqa: PLC0415

    return FixtureHttp({zsdis.PAGE: (FIXTURES / "zsdis" / "casy-prepinania.html").read_bytes()})


def poland_http() -> FixtureHttp:
    """Serve Tauron Dystrybucja's calculator page as captured."""
    from custom_components.powerplan.core.tariffs.sources import tauron  # noqa: PLC0415

    return FixtureHttp({tauron.PAGE: (FIXTURES / "tauron" / "taniej.html").read_bytes()})


def australia_http() -> FixtureHttp:
    """Serve the CDR register, GEE Energy's plans and its captured demand plan."""
    from custom_components.powerplan.core.tariffs.sources import cdr_energy  # noqa: PLC0415

    folder = FIXTURES / "cdr"
    base = "https://cdr.energymadeeasy.gov.au/gee-energy"
    return FixtureHttp(
        {
            cdr_energy.REGISTER: (folder / "register-brands.json").read_bytes(),
            cdr_energy.plans_url(base): (folder / "gee-plans.json").read_bytes(),
            cdr_energy.plan_url(base, "GEE1037096MRE1@EME"): (
                folder / "gee-GEE1037096MRE1.json"
            ).read_bytes(),
        }
    )


def us_http() -> FixtureHttp:
    """Serve URDB's rates for ZIP 85004 and APS's R-3 in full."""
    from custom_components.powerplan.core.tariffs.sources import openei_urdb  # noqa: PLC0415

    folder = FIXTURES / "openei"
    return FixtureHttp(
        {
            openei_urdb.rates_url("85004"): (folder / "rates-85004.json").read_bytes(),
            openei_urdb.rate_url("69a718961822c9da260daf1b"): (
                folder / "aps-r3-69a718961822c9da260daf1b.json"
            ).read_bytes(),
        }
    )


#: The Norwegian companies whose tables WP4.6 read by hand, as fri-nettleie lists them.
NORWAY: Final = (
    ("tensio-ts", "Tensio TS", "no/tensio-ts"),
    ("tensio-tn", "Tensio TN", "no/tensio-tn"),
    ("elvia", "Elvia", "no/elvia"),
)


class NorwayFixture:
    """fri-nettleie's key, serving the fixture tables as fetched copies (D9 §5.15).

    The flows pick a grid company the way a household does - from a source's list -
    and get the table the tests always had, now a test fixture and not a shipped file.
    """

    key: ClassVar[str] = "fri_nettleie"
    country: ClassVar[str] = "NO"
    tier: ClassVar[Tier] = Tier.T1B
    licence: ClassVar[str | None] = "CC BY 4.0"
    credit: ClassVar[Credit | None] = Credit(
        "Fri Nettleie", "https://github.com/kraftsystemet/fri-nettleie", "CC BY 4.0"
    )

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """List the three companies."""
        return [Operator(key, name) for key, name, _ in NORWAY]

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the company's fixture table as the copy fri-nettleie would give."""
        from tests.builders.presets import fixture_raw  # noqa: PLC0415

        name = next(file for key, _, file in NORWAY if key == operator)
        grid = from_preset(fixture_raw(name), source="fri_nettleie", zone=TaxZone("NO")).grid
        return Fetched(
            grid=replace(
                grid,
                provenance=Provenance(
                    source="fri_nettleie", url="https://example.invalid", tier="T1b"
                ),
                operator_key=operator,
                product_key=product,
            )
        )


class FlandersFixture:
    """VREG's key, serving Fluvius Imewo's fixture table as the fetched copy."""

    key: ClassVar[str] = "vreg_xlsx"
    country: ClassVar[str] = "BE"
    tier: ClassVar[Tier] = Tier.T6
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit(
        "Vlaamse Nutsregulator", "https://www.vlaamsenutsregulator.be", None
    )

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """List Fluvius Imewo."""
        return [Operator("fi", "Fluvius Imewo")]

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return Imewo's fixture table as the copy VREG's sheet would give."""
        from tests.builders.presets import fixture_raw  # noqa: PLC0415

        grid = from_preset(
            fixture_raw("be/fluvius-imewo"), source="vreg_xlsx", zone=TaxZone("BE")
        ).grid
        return Fetched(
            grid=replace(
                grid,
                provenance=Provenance(source="vreg_xlsx", url="https://example.invalid", tier="T6"),
                operator_key=operator,
            )
        )
