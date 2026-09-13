"""fri-nettleie's fetcher: the archive, NVE's zones, the credit, the downloads let go (D13 §5).

D13 §19 1 and 22; §5.2 rules 2 and 6; §11. The archive and NVE's list are the
captured ones; no socket opens.
"""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.tariffs.sources import QualityError, Tier, fri_nettleie
from custom_components.powerplan.providers.tariffs import base
from custom_components.powerplan.providers.tariffs.fri_nettleie import FriNettleie, unpack
from tests.builders.tariff_sources import fri_http

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PROVIDERS = Path(__file__).resolve().parents[3] / "custom_components" / "powerplan" / "providers"


def test_22_registered_for_norway_as_t1b_with_its_credit() -> None:
    """CC BY 4.0 asks for credit: the source names Fri Nettleie, its repository and licence."""
    base.register(FriNettleie)
    assert base.for_country("NO") == [FriNettleie]
    assert FriNettleie.tier is Tier.T1B
    assert FriNettleie.credit is not None
    assert (FriNettleie.credit.name, FriNettleie.credit.licence) == ("Fri Nettleie", "CC BY 4.0")


async def test_1_the_list_and_the_copy_come_from_one_archive() -> None:
    """73 companies with a household tariff; Tensio TS's copy is fri-nettleie's, excl. VAT."""
    http = fri_http()
    source = FriNettleie()
    operators = await source.operators(http)  # type: ignore[arg-type]
    assert len(operators) == 73
    fetched = await source.fetch(http, "tensio-ts", None, {})  # type: ignore[arg-type]
    assert fetched.grid.provenance.source == fri_nettleie.KEY
    assert fetched.grid.provenance.url.startswith("https://")
    assert fetched.grid.renew_at is None or fetched.grid.renew_at > http.day
    assert http.asked.count(fri_nettleie.TARBALL) == 1, "downloaded and parsed once for both"


async def test_1_a_company_the_archive_does_not_hold_is_a_quality_failure() -> None:
    """The ladder falls to the next tier rather than inventing a tariff."""
    with pytest.raises(QualityError):
        await FriNettleie().fetch(fri_http(), "no-such-company", None, {})  # type: ignore[arg-type]


def _archive(*members: tuple[str, bytes | None]) -> bytes:
    """Build a gzip tarball; a `None` body makes the member a symbolic link."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            if body is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def test_11_a_link_in_the_archive_is_never_followed() -> None:
    """Members are read, never extracted: a link named like a tariff is skipped."""
    archive = _archive(
        ("repo/tariffer/link.yml", None),
        ("repo/tariffer/real.yml", b"netteier: Real AS\ntariffer: []\n"),
    )
    assert list(unpack(archive).documents) == ["real"]


@pytest.mark.parametrize(
    "archive",
    [b"not a tarball", _archive(("repo/README.md", b"# nothing here"))],
    ids=["garbage", "no-tariffs"],
)
def test_11_an_archive_without_tariffs_is_a_quality_failure(archive: bytes) -> None:
    """Anything but the repository's layout is refused, not guessed at."""
    with pytest.raises(QualityError):
        unpack(archive)


def test_5_2_rule_6_no_tariff_download_is_written_to_disk() -> None:
    """The fetchers hold what they download in memory: no file, no temp dir, no extraction."""
    writes = re.compile(
        r"\bopen\(|write_bytes|write_text|tempfile|extractall|\.extract\(|mkdtemp|NamedTemporary"
    )
    found = [
        f"{path.name}: {line.strip()}"
        for path in sorted((PROVIDERS / "tariffs").glob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if writes.search(line) and "tarfile.open(fileobj=" not in line
    ]
    assert found == []


async def test_5_2_rule_6_release_drops_every_download(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Http` downloads a URL once; `release()` lets it go."""
    calls: list[str] = []

    async def download(self: base.Http, url: str, headers: object) -> bytes:
        calls.append(url)
        return b"body"

    monkeypatch.setattr(base.Http, "_download", download)
    http = base.Http(hass)
    assert await http.get("https://example.invalid/a") == b"body"
    assert await http.get("https://example.invalid/a") == b"body"
    assert calls == ["https://example.invalid/a"]
    http.release()
    await http.get("https://example.invalid/a")
    assert len(calls) == 2, "released: a later call downloads again"


#: The captured archive, read once at import (a test's async body does no file I/O).
ARCHIVE = next(
    (Path(__file__).resolve().parents[2] / "fixtures" / "tariff_sources" / "fri_nettleie").glob(
        "*.tar.gz"
    )
).read_bytes()


class _Content:
    """An aiohttp body that arrives in pieces, as a real archive does."""

    def __init__(self, body: bytes, piece: int) -> None:
        self.pieces = [body[i : i + piece] for i in range(0, len(body), piece)]

    async def read(self, n: int = -1) -> bytes:
        """`StreamReader.read(n)`: whatever has arrived - the first piece."""
        return self.pieces[0]

    async def iter_chunked(self, n: int):  # type: ignore[no-untyped-def]
        """Every piece, to the end."""
        for piece in self.pieces:
            yield piece


class _Answer:
    def __init__(self, body: bytes, piece: int = 4096) -> None:
        self.content = _Content(body, piece)


async def test_11_a_download_is_read_to_its_end_not_to_its_first_piece() -> None:
    """A postcode in the wizard: 4 767 of 404 716 bytes, then "Unknown error"."""
    body = ARCHIVE
    assert len(await base._whole(_Answer(body))) == len(body)
    unpack(await base._whole(_Answer(body)))


async def test_11_a_download_past_the_cap_stops_one_piece_over() -> None:
    """D13 §11: nothing over 5 MB is read, however it arrives."""
    body = b"x" * (base.MAX_BYTES + 200_000)
    assert base.MAX_BYTES < len(await base._whole(_Answer(body, 65_536))) <= base.MAX_BYTES + 65_536


def test_11_a_cut_archive_is_a_quality_failure_the_ladder_can_pass() -> None:
    """A truncated gzip is the source's failure, never an unknown error in the wizard."""
    body = ARCHIVE
    with pytest.raises(QualityError):
        unpack(body[:4767])
