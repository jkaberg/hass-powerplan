"""Fake tariff sources - one per tier, no network (D9 §5.15, D13 §19 15).

A fake lists the operators it is told to, and answers with Tensio TS's copy, a
quality failure or an unreachable endpoint, so the flow and the ladder can be
walked exactly as a real adapter would drive them.
"""

from __future__ import annotations

from dataclasses import replace
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
    from collections.abc import Mapping

    from custom_components.powerplan.providers.tariffs import Http

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

        async def operators(self, http: Http) -> list[Operator]:
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
