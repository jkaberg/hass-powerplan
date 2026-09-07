"""D13 §19 15 and the registry half of §19 12 - the ladder, and every source's credit.

INV-75: a company's tariff comes from the first tier that exists and passes the
quality check - T1 before T2 before T4 before T6. The fakes never touch the
network; a fake that fails the check or cannot be reached hands over to the next.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.tariffs.sources import Credit, Tier
from custom_components.powerplan.providers.tariffs import base, ladder
from tests.builders.tariff_sources import fake

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def registered() -> Iterator[list[str]]:
    """Take away every fake a test registers."""
    keys: list[str] = []
    yield keys
    for key in keys:
        base.unregister(key)


class NoHttp:
    """An `Http` that must not be used: the fakes answer on their own."""


@pytest.mark.inv("INV-75")
async def test_15_the_best_tier_that_answers_is_the_source(registered: list[str]) -> None:
    """A T6 document, a T4 page and a T1a API all list the company: T1a is used."""
    for key, tier in (("doc", Tier.T6), ("page", Tier.T4), ("api", Tier.T1A), ("key", Tier.T3)):
        base.register(fake(key, tier))
        registered.append(key)
    assert [cls.tier for cls in base.for_country("NO")] == [Tier.T1A, Tier.T3, Tier.T4, Tier.T6]
    resolved = await ladder.resolve(NoHttp(), "NO", "Tensio TS")  # type: ignore[arg-type]
    assert resolved.source.key == "api"
    assert resolved.fetched.grid.provenance.tier == "T1a"


@pytest.mark.inv("INV-75")
async def test_15_a_tier_that_fails_the_check_or_is_down_falls_to_the_next(
    registered: list[str],
) -> None:
    """T1a is unreachable, T1b labels its basis wrong, T4 answers: T4 is the source."""
    sources = [
        fake("country", Tier.T1A, behaviour="down"),
        fake("aggregator", Tier.T1B, behaviour="quality"),
        fake("page", Tier.T4),
        fake("doc", Tier.T6),
    ]
    resolved = await ladder.resolve(NoHttp(), "NO", "tensio-ts", sources=sources)  # type: ignore[arg-type]
    assert resolved.source.key == "page"
    assert sources[3].calls == [], "a lower tier is never asked once a higher one answered"


async def test_15_a_source_that_does_not_list_the_company_is_passed_over() -> None:
    """The company is not on T1a's list; T2 has it."""
    sources = [fake("country", Tier.T1A, operators=()), fake("company", Tier.T2)]
    resolved = await ladder.resolve(NoHttp(), "NO", "Tensio TS", sources=sources)  # type: ignore[arg-type]
    assert resolved.source.key == "company"


async def test_15_no_tier_answering_is_said_so() -> None:
    """Every tier fails: the flow offers the rule template and "enter it myself" (§13)."""
    sources = [fake("a", Tier.T1A, behaviour="down"), fake("b", Tier.T4, behaviour="quality")]
    with pytest.raises(ladder.NoSourceError, match=r"a: .*503.*b: .*basis"):
        await ladder.resolve(NoHttp(), "NO", "Tensio TS", sources=sources)  # type: ignore[arg-type]


def test_12_a_licence_that_asks_for_credit_cannot_be_registered_without_it(
    registered: list[str],
) -> None:
    """fri-nettleie is CC BY 4.0: registering it without its credit fails (§6.1)."""
    bare = fake("bare", Tier.T1B, licence="CC BY 4.0", credit=Credit("x", "https://x.invalid"))
    with pytest.raises(ValueError, match="requires credit"):
        base.register(bare)
    credited = fake("credited", Tier.T1B, licence="CC BY 4.0")
    base.register(credited)
    registered.append("credited")
    assert "credited" in base.keys()  # noqa: SIM118 - the registry function
