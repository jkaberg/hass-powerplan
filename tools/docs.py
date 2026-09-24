#!/usr/bin/env python3
"""Write and check the generated blocks of the user pages (D14 §3.2, §5.6).

A page holds a fact the code already knows between two markers:

    <!-- generated:begin actions · tools/docs.py writes this block; change the source, not this table -->
    …
    <!-- generated:end actions -->

`--write` rewrites every block from its source (registries, `strings.json`,
`services.yaml`, `manifest.json`); `--check` exits 1 and names each stale block;
`--external` fetches every outbound link on the pages and names the ones that
fail - run it before a release, since CI has no network (D14 §10).

The `entities:<device>` blocks come from the entity registry of the reference
house, which only a running Home Assistant has: `--write` runs
`tests/docs/test_generated.py` with `POWERPLAN_DOCS_WRITE=1`, and that test is
their `--check` (D-0540).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.powerplan.core.loads.types import base as device_types  # noqa: E402
from custom_components.powerplan.core.pricing.modifiers import registry as modifiers  # noqa: E402
from custom_components.powerplan.core.strategies import base as strategies  # noqa: E402
from custom_components.powerplan.core.tariffs import countries  # noqa: E402
from custom_components.powerplan.dashboard.layout import CUSTOM_CARDS, VIEWS  # noqa: E402
from custom_components.powerplan.events import ENVELOPE, SCHEMAS, event_name  # noqa: E402
from custom_components.powerplan.providers.prices.formats import registry as formats  # noqa: E402
from custom_components.powerplan.providers.profiles import registry as profiles  # noqa: E402
from custom_components.powerplan.providers.tariffs import base as tariff_sources  # noqa: E402
from custom_components.powerplan.repairs import CATALOGUE  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

DOCS = REPO_ROOT / "docs"
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"

BLOCK = re.compile(
    r"(?P<begin><!-- generated:begin (?P<id>\S+)[^\n]*-->\n)(?P<body>.*?)"
    r"(?P<end><!-- generated:end (?P=id) -->)",
    re.DOTALL,
)
#: Blocks the reference house's registry fills, through pytest (module docstring).
HOUSE_BLOCKS = re.compile(r"^entities:")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


def strings() -> dict[str, Any]:
    """Return the integration's `strings.json` (the en labels)."""
    loaded: dict[str, Any] = json.loads((INTEGRATION / "strings.json").read_text("utf-8"))
    return loaded


def _options(key: str) -> dict[str, str]:
    options: dict[str, str] = strings()["selector"].get(key, {}).get("options", {})
    return options


def _label(selector: str, key: str) -> str:
    return _options(selector).get(key, key)


def _cell(text: str) -> str:
    """Return `text` as one table cell: links unwrapped, pipes escaped, placeholders named."""
    text = _LINK.sub(r"\1", text).replace("|", "\\|").replace("\n", " ")
    return _PLACEHOLDER.sub(
        lambda m: (
            f"\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}{m.group(1)}\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
        ),
        text,
    ).strip()


def _table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _code(key: str) -> str:
    return f"`{key}`"


# --------------------------------------------------------------------------- #
# Renderers, one per block id (D14 §5.6)
# --------------------------------------------------------------------------- #


def render_requirements(_arg: str | None) -> str:
    """`install.md`: the HA floor and what PowerPlan works with (`hacs.json`, `manifest.json`)."""
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text("utf-8"))
    manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
    return _table(
        ("Requirement", "Value"),
        (
            ("Home Assistant", f"{hacs['homeassistant']} or newer"),
            ("Uses, when set up", ", ".join(_code(d) for d in manifest["after_dependencies"])),
            ("Installs", ", ".join(_code(r) for r in manifest["requirements"])),
            ("Version", manifest["version"]),
        ),
    )


def _flow(flow: str) -> dict[str, Any]:
    data = strings()
    body: dict[str, Any] = data["config"] if flow == "config" else data["config_subentries"][flow]
    return body


def render_fields(arg: str | None) -> str:
    """Render a flow step's questions and what each asks; `arg` is `<flow>.<step>`."""
    assert arg is not None
    flow, _, step_id = arg.partition(".")
    step = _flow(flow)["step"][step_id]
    rows: list[tuple[str, str]] = []
    for key, label in step.get("data", {}).items():
        rows.append((label, step.get("data_description", {}).get(key, "")))
    for section in step.get("sections", {}).values():
        for key, label in section.get("data", {}).items():
            what = section.get("data_description", {}).get(key, "")
            rows.append(
                (
                    f"{section.get('name', '')} \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} {label}",
                    what,
                )
            )
    return _table(("Question", "What it asks"), rows)


def render_questions(arg: str | None) -> str:
    """Render one appliance type's own questions, in its questionnaire's order (DOC.3).

    The appliance flow's `questions` step is shared by the eight types, so its
    `fields` table would list them all; a type's page lists only its own.
    """
    assert arg is not None
    step = _flow("load")["step"]["questions"]
    advanced = step.get("sections", {}).get("advanced", {})
    section = advanced.get("name", "")
    rows: list[tuple[str, str]] = []
    for question in device_types.get(arg).questionnaire.questions:
        key = question.key
        if key in step.get("data", {}):
            label, what = step["data"][key], step.get("data_description", {}).get(key, "")
        elif key in advanced.get("data", {}):
            label = (
                f"{section} \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} {advanced['data'][key]}"
            )
            what = advanced.get("data_description", {}).get(key, "")
        else:
            continue
        rows.append((label, what))
    return _table(("Question", "What it asks"), rows)


def render_types(_arg: str | None) -> str:
    """`appliances/README.md`: every device type, its default plan and the others."""
    rows = []
    for key in device_types.keys():  # noqa: SIM118 - a registry, not a dict
        default = device_types.get(key).default_strategy
        others = [s for s in strategies.supports(key) if s != default]
        rows.append(
            (
                _label("load_type", key),
                _label("strategy", default),
                ", ".join(_label("strategy", s) for s in others) or "—",
                _code(key),
            )
        )
    return _table(("Appliance", "Default plan", "Other plans", "Key"), rows)


def render_strategies(_arg: str | None) -> str:
    """`strategies.md`: every plan, what it is offered for, and where it is the default."""
    type_keys = device_types.keys()
    rows = []
    for key in strategies.keys():  # noqa: SIM118 - a registry, not a dict
        offered = [t for t in type_keys if key in strategies.supports(t)]
        default = [t for t in type_keys if device_types.get(t).default_strategy == key]
        rows.append(
            (
                _label("strategy", key),
                ", ".join(_label("load_type", t) for t in offered) or "—",
                ", ".join(_label("load_type", t) for t in default) or "—",
                _code(key),
            )
        )
    return _table(("Plan", "Offered for", "Default for", "Key"), rows)


#: How a control kind steers, in the household's words (D4 §5).
_KINDS = {
    "battery": "charges, holds and discharges it",
    "modulate": "sets the power or current",
    "switch": "turns it on and off",
    "mode": "sets its mode",
    "setpoint": "sets its temperature",
}


def render_profiles(_arg: str | None) -> str:
    """`devices.md`: every device profile, the appliances it serves and how it steers them."""
    rows = []
    for key in profiles.keys():  # noqa: SIM118 - a registry, not a dict
        profile = profiles.get(key)
        rows.append(
            (
                _code(key),
                ", ".join(_label("load_type", t) for t in sorted(profile.types)),
                ", ".join(_KINDS.get(k, k) for k in sorted(profile.kinds)),
            )
        )
    return _table(("Profile", "For", "PowerPlan"), rows)


def render_formats(_arg: str | None) -> str:
    """`prices.md`: every price format, the integration it reads and how."""
    rows = []
    for key in formats.keys():  # noqa: SIM118 - a registry, not a dict
        entry = formats.entry(key)
        how = "an action's response" if entry.kind.value == "action" else "a sensor"
        rows.append(
            (
                _label("price_format", key),
                _code(entry.platform) if entry.platform else "any",
                how,
                _code(key),
            )
        )
    return _table(("Price source", "Integration", "Read from", "Key"), rows)


def render_modifiers(_arg: str | None) -> str:
    """`prices.md`: every price add-on and the questions it asks (the flow's own step)."""
    steps = _flow("config")["step"]
    rows = []
    for key in modifiers.keys():  # noqa: SIM118 - a registry, not a dict
        asks = steps.get(f"modifier_{key}", {}).get("data", {}).values()
        rows.append((_label("modifier", key), ", ".join(asks) or "—", _code(key)))
    return _table(("Price add-on", "Asks for", "Key"), rows)


#: Where a grid source's prices come from, in the household's words (D13 §5.1's tiers).
_TIERS = {
    "T1a": "the regulator's or the country's own open data",
    "T1b": "a national data service",
    "T2": "the grid company's open tariff service",
    "T3": "your own account at the grid company",
    "T4": "the grid company's own website",
    "T5": "a community-kept list",
    "T6": "the regulator's published tariff sheet",
}


def _source_name(cls: Any) -> str:
    credit = cls.credit
    if credit is None:
        return _code(cls.key)
    return f"[{credit.name}]({credit.url})"


def _vat(module: Any) -> str:
    """Every dated VAT rate of a module, as the table says it: `21 %; 10 % from 2026-08-01, to 10 kW`."""
    if not module.vat:
        return "asked"
    parts = []
    for rate in module.vat:
        text = f"{(rate.value * 100).normalize():f} %"
        if rate.valid_from is not None:
            text += f" from {rate.valid_from.isoformat()}"
        if rate.upto_kw is not None:
            text += f", to {rate.upto_kw:g} kW"
        parts.append(text)
    return "; ".join(parts)


def render_countries(_arg: str | None) -> str:
    """`tariffs.md`: every country module, its grid sources in ladder order, VAT and what setup asks."""
    rows = []
    for code in sorted(countries.codes()):
        module = countries.get(code)
        assert module is not None
        sources = [_source_name(cls) for cls in tariff_sources.for_country(code)]
        asks = []
        if module.postcode is not None:
            asks.append("your postcode")
        if module.zones:
            asks.append("your region, where its taxes differ")
        if not sources:
            asks.append(
                "the grid tariff from your bill"
                if module.rule_template
                else "your grid tariff, typed in"
            )
        rows.append(
            (
                module.name,
                ", ".join(sources) or "—",
                _vat(module),
                "; ".join(asks) or "nothing more",
            )
        )
    return _table(("Country", "Grid tariff fetched from", "VAT", "Setup also asks"), rows)


def render_sources(_arg: str | None) -> str:
    """`tariffs.md`: every grid source, its countries, where its prices come from, its credit."""
    rows = []
    for key in tariff_sources.keys():  # noqa: SIM118 - a registry, not a dict
        cls = tariff_sources.get(key)
        module = countries.get(cls.country)
        credit = cls.credit
        licence = "" if credit is None or credit.licence is None else f" ({credit.licence})"
        rows.append(
            (
                _source_name(cls) + licence,
                module.name if module is not None else cls.country,
                _TIERS.get(cls.tier.value, cls.tier.value),
                _code(key),
            )
        )
    return _table(("Source", "Country", "Where the prices come from", "Key"), rows)


def render_repairs(_arg: str | None) -> str:
    """`troubleshooting.md`: every repair's title and whether it offers a fix."""
    issues = strings()["issues"]
    rows = [
        (issues[key]["title"], "yes" if CATALOGUE[key].fixable else "no", _code(key))
        for key in CATALOGUE
    ]
    return _table(("Repair", "Offers a fix", "Key"), rows)


def render_actions(_arg: str | None) -> str:
    """`actions.md`: every action, what it does and its fields (`services.yaml`, `strings.json`)."""
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text("utf-8"))
    texts = strings()["services"]
    rows = []
    for name, spec in services.items():
        fields = []
        for key, field in (spec or {}).get("fields", {}).items():
            required = " (required)" if field.get("required") else ""
            fields.append(f"`{key}`{required}")
        rows.append(
            (
                f"`powerplan.{name}`",
                texts[name]["name"],
                # The link points back at this page (D14 §5.4): not repeated in the table.
                re.sub(r"\s*\[[^\]]*\]\(\{docs\}\)", "", texts[name]["description"]),
                ", ".join(fields) or "—",
            )
        )
    return _table(("Action", "Name", "What it does", "Fields"), rows)


def render_events(_arg: str | None) -> str:
    """`events.md`: every bus event, its logbook label and its payload keys (D8 §5.6)."""
    labels = strings()["entity"]["event"]["events"]["state_attributes"]["event_type"]["state"]
    rows = []
    for kind, schema in SCHEMAS.items():
        keys = sorted(str(k) for k in schema.schema)
        rows.append(
            (
                f"`{event_name(kind)}`",
                labels[kind.value],
                ", ".join(_code(k) for k in keys),
            )
        )
    envelope = ", ".join(_code(k) for k in sorted(ENVELOPE))
    return _table(("Event", "Shown as", "Payload, besides " + envelope), rows)


def render_dashboard_options(_arg: str | None) -> str:
    """`dashboard.md`: the dashboard's options and the values each takes (D12 §5.1)."""
    return _table(
        ("Option", "Values"),
        (
            ("`hidden_views`", ", ".join(_code(v) for v in VIEWS)),
            ("`hidden_cards`", ", ".join(_code(c) for c in sorted(CUSTOM_CARDS))),
        ),
    )


RENDERERS: dict[str, Callable[[str | None], str]] = {
    "requirements": render_requirements,
    "fields": render_fields,
    "questions": render_questions,
    "types": render_types,
    "strategies": render_strategies,
    "profiles": render_profiles,
    "formats": render_formats,
    "modifiers": render_modifiers,
    "countries": render_countries,
    "sources": render_sources,
    "repairs": render_repairs,
    "actions": render_actions,
    "events": render_events,
    "dashboard_options": render_dashboard_options,
}


def render(block_id: str) -> str:
    """Return the body of one block, by its id (`fields:config.tariff`, `actions`)."""
    name, _, arg = block_id.partition(":")
    return RENDERERS[name](arg or None)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


def pages() -> list[Path]:
    """Return every user page, sorted."""
    return sorted(DOCS.rglob("*.md"))


def blocks(text: str) -> Iterator[re.Match[str]]:
    """Yield every generated block of a page."""
    return BLOCK.finditer(text)


def replace(text: str, block_id: str, body: str) -> str:
    """Return `text` with the body of block `block_id` replaced."""

    def sub(match: re.Match[str]) -> str:
        if match["id"] != block_id:
            return match[0]
        return match["begin"] + body + match["end"]

    return BLOCK.sub(sub, text)


def stale(path: Path) -> list[str]:
    """Return the ids of the blocks of `path` whose body differs from its source."""
    text = path.read_text("utf-8")
    return [
        m["id"]
        for m in blocks(text)
        if not HOUSE_BLOCKS.match(m["id"]) and m["body"] != render(m["id"])
    ]


def write(path: Path) -> list[str]:
    """Rewrite the blocks of `path` from their sources; return the ids that changed."""
    text = path.read_text("utf-8")
    changed = stale(path)
    for block_id in changed:
        text = replace(text, block_id, render(block_id))
    if changed:
        path.write_text(text, "utf-8")
    return changed


def _has_house_blocks() -> bool:
    return any(
        HOUSE_BLOCKS.match(m["id"]) for path in pages() for m in blocks(path.read_text("utf-8"))
    )


def external_links() -> dict[str, list[Path]]:
    """Return every outbound link on the pages, with the pages that carry it."""
    found: dict[str, list[Path]] = {}
    for path in pages():
        for url in re.findall(r"\]\((https?://[^)\s]+)\)", path.read_text("utf-8")):
            found.setdefault(url, []).append(path)
    return found


def _reachable(url: str) -> bool:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "powerplan-docs"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            return True
    except urllib.error.URLError, TimeoutError:
        return False


def main(argv: list[str] | None = None) -> int:
    """Run `--write`, `--check` or `--external`; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite every generated block")
    mode.add_argument("--check", action="store_true", help="exit 1 when a block is stale")
    mode.add_argument("--external", action="store_true", help="fetch every outbound link")
    args = parser.parse_args(argv)

    if args.external:
        broken = [
            (url, where) for url, where in sorted(external_links().items()) if not _reachable(url)
        ]
        for url, where in broken:
            print(f"{url}  ({', '.join(str(p.relative_to(REPO_ROOT)) for p in where)})")
        return 1 if broken else 0

    if args.check:
        found = [(path, block_id) for path in pages() for block_id in stale(path)]
        for path, block_id in found:
            print(f"stale: {path.relative_to(REPO_ROOT)} {block_id}")
        if found:
            print("run: uv run python tools/docs.py --write")
        return 1 if found else 0

    for path in pages():
        for block_id in write(path):
            print(f"wrote: {path.relative_to(REPO_ROOT)} {block_id}")
    if _has_house_blocks():
        env = {**os.environ, "POWERPLAN_DOCS_WRITE": "1"}
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/docs/test_generated.py",
            "-q",
            "-k",
            "house",
        ]
        return subprocess.run(command, cwd=REPO_ROOT, env=env, check=False).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
