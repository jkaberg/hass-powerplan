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
from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.household import Provenance, TaxZone, from_preset
from custom_components.powerplan.core.tariffs.rules import loader
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
    grid = from_preset(loader.load_raw("no/tensio-ts"), source=source, zone=TaxZone("NO")).grid
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
