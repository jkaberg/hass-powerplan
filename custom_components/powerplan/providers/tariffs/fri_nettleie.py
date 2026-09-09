"""Fri Nettleie's fetcher: one tarball, NVE's zones, CC BY 4.0 (D13 §5.4, §5.11; O16).

The repository is read as one archive of its default branch - one request for
every company, not seventy-five - and only its `tariffer/*.yml` and Elhub's
`grid_owners.json` are opened; nothing is extracted to disk. NVE's county list
gives each company its tax zones. The YAML is parsed in the executor; the rules
are `core/tariffs/sources/fri_nettleie.py`'s.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Final

import yaml

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    QualityError,
    Tier,
    fri_nettleie,
    nve,
)

from .base import MAX_BYTES, register

if TYPE_CHECKING:
    from .base import Http

__all__ = ["Bundle", "FriNettleie", "unpack"]

_TARIFFS: Final = "/tariffer/"
_OWNERS: Final = "/referanse-data/elhub/grid_owners.json"


@dataclass(frozen=True, slots=True)
class Bundle:
    """The archive's documents by file stem, and Elhub's GLN → organisation number."""

    documents: Mapping[str, Mapping[str, Any]]
    organisations: Mapping[str, str]


def unpack(archive: bytes) -> Bundle:
    """Return the tariff files and the GLN map; `QualityError` on anything else (§11).

    Members are read, never extracted: a link, a device or an oversized member
    is refused rather than followed.
    """
    documents: dict[str, Mapping[str, Any]] = {}
    organisations: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                folder, _, file = member.name.rpartition("/")
                # `tariffer/old/` keeps companies merged away: never offered.
                wanted = member.name.endswith(_OWNERS) or (
                    f"{folder}/".endswith(_TARIFFS) and file.endswith(".yml")
                )
                if not wanted or not member.isfile() or member.size > MAX_BYTES:
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                body = handle.read()
                if member.name.endswith(_OWNERS):
                    organisations = {
                        str(row["gln"]): str(row["organisationNumber"]) for row in json.loads(body)
                    }
                else:
                    stem = file.removesuffix(".yml")
                    documents[stem] = yaml.safe_load(body)
    except (tarfile.TarError, OSError, yaml.YAMLError, ValueError, KeyError) as err:
        msg = f"{fri_nettleie.KEY}: unreadable archive: {err}"
        raise QualityError(msg) from err
    if not documents:
        msg = f"{fri_nettleie.KEY}: the archive holds no tariff files"
        raise QualityError(msg)
    return Bundle(documents, organisations)


@register
class FriNettleie:
    """Norway's T1b: every grid company's household tariff, excl. VAT and levies."""

    key: ClassVar[str] = fri_nettleie.KEY
    country: ClassVar[str] = "NO"
    tier: ClassVar[Tier] = Tier.T1B
    licence: ClassVar[str | None] = "CC BY 4.0"
    credit: ClassVar[Credit | None] = Credit("Fri Nettleie", fri_nettleie.REPOSITORY, "CC BY 4.0")

    async def _bundle(self, http: Http) -> Bundle:
        return await http.document(fri_nettleie.TARBALL, unpack)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return every company with a household tariff, each with its counties' zones."""
        bundle = await self._bundle(http)
        month = http.today().replace(day=1)
        counties = nve.counties(await http.get(nve.URL.format(day=month.isoformat())))
        return fri_nettleie.operators(bundle.documents, bundle.organisations, counties)

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one company's copy, parsed with the household's answers."""
        bundle = await self._bundle(http)
        doc = bundle.documents.get(operator)
        if doc is None:
            msg = f"{fri_nettleie.KEY}: no file for {operator}"
            raise QualityError(msg)
        return fri_nettleie.parse(
            operator, doc, product=product, fetched=http.today(), answers=answers
        )
