"""The dashboard's layout (D12 §5.1): a `SiteLayout` in, a Lovelace config out.

Four `sections` views in the Energy dashboard's shape - `overview`, `plan`,
`loads`, `history` - each `max_columns: 3` with dense placement, and the
history view's period picker in its footer under `energy_powerplan`, the key
every graph there follows. Built-in cards wherever one can show the thing;
the timeline and the window gauge are the two custom cards (D12 §5.2, §5.3).
A card whose entity is not shown is left out, and a section left with only its
heading goes with it (D12 §8). Plain data in, plain data out: no `hass`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from awesomeversion import AwesomeVersion

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from .site_layout import LoadLayout, SiteLayout

__all__ = ["COLLECTION_KEY", "CUSTOM_CARDS", "FEATURES", "VIEWS", "build"]

#: The history view's period collection; a key must start with `energy_` (D12 §2).
COLLECTION_KEY = "energy_powerplan"
#: The only card types not shipped with HA (D12 §9 1).
TIMELINE_CARD = "custom:powerplan-timeline-card"
WINDOW_CARD = "custom:powerplan-window-card"
CUSTOM_CARDS = frozenset({TIMELINE_CARD, WINDOW_CARD})
VIEWS: tuple[str, ...] = ("overview", "plan", "loads", "history")
_VIEW_ICONS = {
    "overview": "mdi:home-lightning-bolt",
    "plan": "mdi:calendar-clock",
    "loads": "mdi:devices",
    "history": "mdi:chart-bar",
}
#: The first HA release with each card or view feature (D12 §5.5): the frontend
#: build each release pins, read at its tag (20260128.6, 20260304.0).
FEATURES: Mapping[str, AwesomeVersion] = {
    "distribution": AwesomeVersion("2026.2.0"),
    "repairs": AwesomeVersion("2026.3.0"),
    "footer": AwesomeVersion("2026.3.0"),
}
_THERMAL = frozenset({"floor_heating", "heat_pump", "radiator", "water_heater"})

type Card = dict[str, Any]


def build(
    sites: Sequence[SiteLayout],
    ha_version: str,
    texts: Mapping[str, str],
    *,
    has_energy_grid: bool = False,
    hidden_views: Collection[str] = (),
    hidden_cards: Collection[str] = (),
) -> dict[str, Any]:
    """Return the dashboard config for `sites` on `ha_version`, headings from `texts`.

    One site gets the four views under their own paths. Several get four views
    each, the site's name before the view's and its entry id after the path, so
    one dashboard shows every site the household has (D12 §5.1).
    """
    version = AwesomeVersion(ha_version)
    has = {feature: version >= first for feature, first in FEATURES.items()}
    several = len(sites) > 1
    views = []
    for site in sites:
        for path in VIEWS:
            if path in hidden_views:
                continue
            sections = [
                kept
                for section in _sections(path, site, texts, has, has_energy_grid=has_energy_grid)
                if (kept := _without(section, hidden_cards)) is not None
            ]
            title = texts[f"view_{path}"]
            view: dict[str, Any] = {
                "title": f"{site.name} · {title}" if several else title,
                "path": f"{path}-{site.entry_id}" if several else path,
                "icon": _VIEW_ICONS[path],
                "type": "sections",
                "max_columns": 3,
                "dense_section_placement": True,
                "sections": sections,
            }
            if path == "history":
                picker = {"type": "energy-date-selection", "collection_key": COLLECTION_KEY}
                if has["footer"]:
                    view["footer"] = {"card": picker}
                else:
                    sections.insert(0, {"type": "grid", "column_span": 3, "cards": [picker]})
            views.append(view)
    return {"title": sites[0].name if len(sites) == 1 else "PowerPlan", "views": views}


# --------------------------------------------------------------------------- #
# The views
# --------------------------------------------------------------------------- #


def _sections(
    path: str,
    site: SiteLayout,
    texts: Mapping[str, str],
    has: Mapping[str, bool],
    *,
    has_energy_grid: bool,
) -> list[Card]:
    if path == "overview":
        return _overview(site, texts, has)
    if path == "plan":
        return _plan(site, texts)
    if path == "loads":
        return _loads(site, texts)
    return _history(site, texts, has_energy_grid=has_energy_grid)


def _overview(site: SiteLayout, texts: Mapping[str, str], has: Mapping[str, bool]) -> list[Card]:
    e = site.entities
    granted = [ids["granted_power"] for ids in _load_ids(site) if "granted_power" in ids]
    power: Card | None = None
    if granted:
        power = (
            {"type": "distribution", "entities": granted}
            if has["distribution"]
            else {"type": "entities", "entities": granted}
        )
    sections = [
        _section(texts["this_window"], _window_card(site, texts)),
        _section(
            texts["controls"],
            _tile(e, "active", {"type": "toggle"}),
            _tile(e, "presence", {"type": "select-options"}),
            _tile(e, "target", {"type": "select-options"}),
        ),
        _section(texts["power"], power),
        _section(texts["next_24h"], _timeline(site, 24, texts)),
        _section(texts["coming_up"], _calendar(e)),
        _section(
            texts["this_month"],
            _statistic(e, "cost"),
            _statistic(e, "savings"),
            _tile(e, "level"),
            _tile(e, "projected_level"),
        ),
        _section(texts["advice"], _tile(e, "advice")),
    ]
    if site.has_production:
        sections.append(
            _section(
                texts["solar"],
                _tile(e, "production", {"type": "trend-graph"}),
                _tile(e, "surplus", {"type": "trend-graph"}),
            )
        )
    if has["repairs"]:
        sections.append(_section(texts["attention"], {"type": "repairs", "hide_empty": True}))
    return [section for section in sections if section is not None]


def _plan(site: SiteLayout, texts: Mapping[str, str]) -> list[Card]:
    e = site.entities
    timeline = _section(texts["next_48h"], _timeline(site, 48, texts))
    if timeline is not None:
        timeline["column_span"] = 3
    sections = [
        timeline,
        _section(
            texts["price_now"],
            _tile(e, "price", {"type": "trend-graph", "hours_to_show": 24}),
            _tile(e, "prices_tomorrow"),
        ),
        _section(
            texts["next_run"],
            *(_tile(ids, "plan_status") for ids in _load_ids(site)),
        ),
        _section(texts["coming_up"], _calendar(e)),
    ]
    return [section for section in sections if section is not None]


def _loads(site: SiteLayout, texts: Mapping[str, str]) -> list[Card]:
    sections = []
    for load in site.loads:
        cards = _load_cards(load)
        if cards:
            heading = {"type": "heading", "heading": load.name, "icon": load.icon}
            sections.append({"type": "grid", "cards": [heading, *cards]})
    if not sections:
        sections.append(
            {"type": "grid", "cards": [{"type": "markdown", "content": texts["no_loads"]}]}
        )
    return sections


def _load_cards(load: LoadLayout) -> list[Card]:
    """Return one appliance's cards by type (D12 §5.1's table, on D8 §5.16's entity set)."""
    e = load.entities
    kind = load.type
    cards = [
        _tile(e, "control", {"type": "select-options"}),
        _tile(e, "plan_status"),
    ]
    if kind in {"ev", "battery"}:
        cards.append(_tile(e, "charge_target", {"type": "numeric-input", "style": "slider"}))
    if kind == "ev":
        cards.append(_tile(e, "charge_min", {"type": "numeric-input", "style": "slider"}))
    if kind == "battery":
        cards.append(_tile(e, "soc"))
    if kind in _THERMAL:
        cards.extend(
            (
                _tile(e, "comfort", {"type": "numeric-input", "style": "buttons"}),
                _tile(e, "follow_presence", {"type": "toggle"}),
            )
        )
    if kind == "water_heater":
        cards.append(_tile(e, "next_legionella"))
    if kind == "appliance_cycle":
        cards.append(_tile(e, "run_now", {"type": "button"}))
    if kind == "generic_switch":
        cards.append(_tile(e, "hours_per_day", {"type": "numeric-input", "style": "buttons"}))
    if "ready_by" in e:
        # No tile feature edits a `time` (D12 §5.1): an entities row does.
        cards.append({"type": "entities", "entities": [e["ready_by"]]})
    cards.extend(
        (
            _tile(e, "granted_power", {"type": "trend-graph"}),
            _statistic(e, "cost_month"),
            _statistic(e, "savings_month"),
        )
    )
    return [card for card in cards if card is not None]


def _history(site: SiteLayout, texts: Mapping[str, str], *, has_energy_grid: bool) -> list[Card]:
    e = site.entities
    ids = _load_ids(site)
    sections = [
        _section(
            texts["cost_savings"],
            _graph([e[k] for k in ("cost", "savings") if k in e], ["change"], "bar"),
        ),
        _section(
            texts["windows"],
            _graph([e[k] for k in ("window_used", "ceiling") if k in e], ["max", "mean"], "line"),
        ),
        _section(texts["capacity_level"], _graph([e[k] for k in ("metric",) if k in e], ["max"])),
        _section(
            texts["energy_per_load"],
            _graph([row["energy"] for row in ids if "energy" in row], ["change"], "bar"),
        ),
        _section(
            texts["cost_per_load"],
            _graph([row["cost_month"] for row in ids if "cost_month" in row], ["change"], "bar"),
        ),
    ]
    if has_energy_grid:
        sections.append(
            _section(
                texts["energy"],
                *(
                    {"type": card, "collection_key": COLLECTION_KEY}
                    for card in ("energy-usage-graph", "energy-devices-graph", "energy-sankey")
                ),
            )
        )
    if "events" in e:
        sections.append(
            _section(
                texts["happened"],
                {"type": "logbook", "target": {"entity_id": [e["events"]]}, "hours_to_show": 24},
            )
        )
    return [section for section in sections if section is not None]


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #


def _load_ids(site: SiteLayout) -> list[Mapping[str, str]]:
    return [load.entities for load in site.loads]


def _section(title: str, *cards: Card | None) -> Card | None:
    """Return a grid section under a heading, or `None` when no card is left."""
    kept = [card for card in cards if card is not None]
    if not kept:
        return None
    return {"type": "grid", "cards": [{"type": "heading", "heading": title}, *kept]}


def _without(section: Card, hidden: Collection[str]) -> Card | None:
    """Return `section` without the card types the strategy hides, `None` if nothing is left."""
    if not hidden:
        return section
    cards = [card for card in section["cards"] if card["type"] not in hidden]
    if all(card["type"] == "heading" for card in cards):
        return None
    return {**section, "cards": cards}


def _tile(entities: Mapping[str, str], key: str, *features: Card) -> Card | None:
    if key not in entities:
        return None
    card: Card = {"type": "tile", "entity": entities[key]}
    if features:
        card["features"] = list(features)
    return card


def _statistic(entities: Mapping[str, str], key: str) -> Card | None:
    """Return this calendar month's change of a monetary total (D8 §5.5)."""
    if key not in entities:
        return None
    return {
        "type": "statistic",
        "entity": entities[key],
        "stat_type": "change",
        "period": {"calendar": {"period": "month"}},
    }


def _calendar(entities: Mapping[str, str]) -> Card | None:
    if "plan_calendar" not in entities:
        return None
    return {"type": "calendar", "entities": [entities["plan_calendar"]], "initial_view": "listWeek"}


def _graph(entity_ids: list[str], stat_types: list[str], chart_type: str = "line") -> Card | None:
    """Return a statistics graph that follows the history view's picker (D12 §9 1)."""
    if not entity_ids:
        return None
    return {
        "type": "statistics-graph",
        "entities": entity_ids,
        "stat_types": stat_types,
        "chart_type": chart_type,
        "energy_date_selection": True,
        "collection_key": COLLECTION_KEY,
    }


def _timeline(site: SiteLayout, hours: int, texts: Mapping[str, str]) -> Card | None:
    """Return the timeline (D12 §5.2): the site plan's slots against the price forecast."""
    e = site.entities
    if "plan" not in e or "price_forecast" not in e:
        return None
    show = ["price", "plan", "ceiling", "baseline"]
    if site.has_production:
        show.append("production")
    return {
        "type": TIMELINE_CARD,
        "entry_id": site.entry_id,
        "hours": hours,
        "loads": [{"id": load.subentry_id, "name": load.name} for load in site.loads],
        "entities": {"plan": e["plan"], "price_forecast": e["price_forecast"]},
        "show": show,
        "currency": site.currency,
        "labels": _labels(texts),
    }


def _window_card(site: SiteLayout, texts: Mapping[str, str]) -> Card | None:
    """Return the window gauge (D12 §5.3) on the rows the site shows."""
    e = site.entities
    if "window_used" not in e:
        return None
    keys = ("window_used", "window_projected", "ceiling", "allowance", "stage", "peak_warning")
    return {
        "type": WINDOW_CARD,
        "entry_id": site.entry_id,
        "entities": {key: e[key] for key in (*keys, "next_peak_warning") if key in e},
        "labels": _labels(texts),
    }


def _labels(texts: Mapping[str, str]) -> dict[str, str]:
    """Return the custom cards' own words, from the integration's translations (D-0440)."""
    return {
        key.removeprefix("card_"): value for key, value in texts.items() if key.startswith("card_")
    }
