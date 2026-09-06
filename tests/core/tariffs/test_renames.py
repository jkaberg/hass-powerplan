"""D2 §9 42 - the O25 renames: the typed rules are the tariff model (D-0530).

`core/tariffs/grammar.py` became `model.py`, `Grammar` became `TariffRule`,
`TariffVersion.grammar` became `.rules`, and the evaluator's protocol,
`TariffModel` until TS.1, became `TariffEvaluator` - so "tariff model" means the
typed rules and nothing else.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from custom_components.powerplan.core import tariffs
from custom_components.powerplan.core.tariffs import Evaluator, TariffEvaluator, TariffVersion

ROOT = Path(__file__).resolve().parents[3]
TREES = ("custom_components", "tests", "tools")
OLD = {"Grammar", "TariffModel"}


def _python_files() -> list[Path]:
    return [path for tree in TREES for path in (ROOT / tree).rglob("*.py")]


def test_42_nothing_imports_the_old_names() -> None:
    offenders: list[str] = []
    for path in _python_files():
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[-1] == "grammar":
                    offenders.append(f"{path}:{node.lineno} from …grammar")
                offenders += [
                    f"{path}:{node.lineno} {alias.name}"
                    for alias in node.names
                    if alias.name in OLD or alias.name == "grammar"
                ]
            elif isinstance(node, ast.Import):
                offenders += [
                    f"{path}:{node.lineno} {alias.name}"
                    for alias in node.names
                    if alias.name.endswith(".grammar")
                ]
            elif isinstance(node, ast.Name) and node.id in OLD:
                offenders.append(f"{path}:{node.lineno} {node.id}")
    assert offenders == []
    assert not (ROOT / "custom_components/powerplan/core/tariffs/grammar.py").exists()
    assert not hasattr(tariffs, "Grammar")
    assert not hasattr(tariffs, "TariffModel")


def test_42_a_version_holds_rules() -> None:
    assert "rules" in TariffVersion.__dataclass_fields__
    assert "grammar" not in TariffVersion.__dataclass_fields__


def test_42_evaluator_satisfies_tariff_evaluator() -> None:
    members = {
        name
        for name, _ in inspect.getmembers(TariffEvaluator)
        if not name.startswith("_") or name == "history"
    } | set(TariffEvaluator.__annotations__)
    missing = {
        name
        for name in members
        if not hasattr(Evaluator, name) and name not in Evaluator.__init__.__code__.co_names
    }
    assert missing == set()
    for name, member in inspect.getmembers(TariffEvaluator, inspect.isfunction):
        if name.startswith("_"):
            continue
        wanted = list(inspect.signature(member).parameters)
        got = list(inspect.signature(getattr(Evaluator, name)).parameters)
        assert got[: len(wanted)] == wanted, name
