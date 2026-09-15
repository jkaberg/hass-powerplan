"""The dashboard's layout (D12 §5.1): `SiteLayout`s in, a Lovelace config out.

Two text tabs - `overview` (now) and `history` (the past, on the Energy
dashboard's own period picker, `energy_powerplan`) - and one subview per
appliance, each a `sections` view with `max_columns: 3` and dense placement.
Section order is the phone's reading order. Built-in cards wherever one can
show the thing; the timeline, the window gauge, the period summary, the price
and the appliances are the custom cards (D12 §5.2, §5.3, §5.7, §5.12), and one
markdown card says why an appliance's plan is what it is (§5.9).
Every appliance keeps one colour from HA's palette on every card (§5.8). A card
whose entity is not shown is left out, and a section left with only its heading
goes with it (D12 §8). Plain data in, plain data out: no `hass`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from awesomeversion import AwesomeVersion

from .site_layout import LoadLayout, SiteLayout

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

__all__ = [
    "COLLECTION_KEY",
    "CUSTOM_CARDS",
    "DASHBOARD",
    "ENTITY_NAMES",
    "FEATURES",
    "HA_GRAPH_PALETTE",
    "VIEWS",
    "build",
    "load_colors",
]

#: The history view's period collection; a key must start with `energy_` (D12 §2).
COLLECTION_KEY = "energy_powerplan"
#: The only card types not shipped with HA (D12 §9 1).
TIMELINE_CARD = "custom:powerplan-timeline-card"
WINDOW_CARD = "custom:powerplan-window-card"
SUMMARY_CARD = "custom:powerplan-period-summary"
PRICE_CARD = "custom:powerplan-price-card"
APPLIANCES_CARD = "custom:powerplan-appliances-card"
ATTENTION_CARD = "custom:powerplan-attention-card"
MONTH_BARS = "custom:powerplan-month-bars"
CUSTOM_CARDS = frozenset(
    {
        TIMELINE_CARD,
        WINDOW_CARD,
        SUMMARY_CARD,
        PRICE_CARD,
        APPLIANCES_CARD,
        ATTENTION_CARD,
        MONTH_BARS,
    }
)
#: The Plan card's rail and the appliances card's name column, in px: equal, so
#: the two time axes line up one above the other (D12 §5.12 R2, §5.15 F13).
RAIL = 256
#: The keys `hidden_views` takes: the two tabs, and every appliance's subview.
VIEWS: tuple[str, ...] = ("overview", "history", "appliances")
#: The dashboard's own URL path; `strategy.ts` puts it in after the call (D12 §5.1).
DASHBOARD = "{dashboard}"
#: The first HA release with each card or view feature (D12 §5.5): the frontend
#: build each release pins, read at its tag (20260128.6, 20260304.0).
FEATURES: Mapping[str, AwesomeVersion] = {
    "footer": AwesomeVersion("2026.3.0"),
}
#: HA's chart palette, `--color-1…53` of the 2026.9 frontend (`20260826.7`), in
#: order: the colour HA's own graphs give the n-th series (D12 §5.8, D-0454).
HA_GRAPH_PALETTE: tuple[str, ...] = (
    "#4269d0", "#f4bd4a", "#ff725c", "#6cc5b0", "#a463f2", "#ff8ab7", "#9c6b4e", "#97bbf5",
    "#01ab63", "#094bad", "#c99000", "#d84f3e", "#49a28f", "#048732", "#d96895", "#8043ce",
    "#7599d1", "#7a4c31", "#6989f4", "#ffd444", "#ff957c", "#8fe9d3", "#62cc71", "#ffadda",
    "#c884ff", "#badeff", "#bf8b6d", "#927acc", "#97ee3f", "#bf3947", "#9f5b00", "#f48758",
    "#8caed6", "#f2b94f", "#eff26e", "#e43872", "#d9b100", "#9d7a00", "#698cff", "#00d27e",
    "#d06800", "#009f82", "#c49200", "#cbe8ff", "#fecddf", "#c27eb6", "#8cd2ce", "#c4b8d9",
    "#f883b0", "#a49100", "#f48800", "#27d0df", "#a04a9b",
)  # fmt: skip
#: A card named after its entity takes the entity's own translated name
#: (`entity.<platform>.<key>.name`), never the site's or the device's (D12 §5.1).
ENTITY_NAMES: Mapping[str, tuple[str, str]] = {
    "comfort": ("number", "comfort"),
    "follow_presence": ("switch", "follow_presence"),
    "charge_target": ("number", "charge_target"),
    "charge_min": ("number", "charge_min"),
    "hours_per_day": ("number", "hours_per_day"),
    "run_now": ("button", "run_now"),
    "next_legionella": ("sensor", "next_legionella"),
    "ready_by": ("time", "ready_by"),
    "granted_power": ("sensor", "granted_power"),
    "production": ("sensor", "production"),
    "surplus": ("sensor", "surplus"),
}
#: `plan_status` states in which an appliance draws power now (D8 §5.16).
RUNNING = ("charging", "running_plan", "run_now")
_THERMAL = frozenset({"floor_heating", "heat_pump", "radiator", "water_heater"})
_CONFIDENCE = ("known", "stale", "estimated", "synthesised")
#: Languages whose decimal mark is a comma, for the markdown tables (D12 §5.9).
_DECIMAL_COMMA = frozenset({"nb", "no", "nn", "da", "sv", "de", "fi", "fr", "nl"})

type Card = dict[str, Any]


def load_colors(loads: Sequence[LoadLayout]) -> dict[str, str]:
    """Return each appliance's colour: HA's palette by priority order, from 0 per site."""
    return {
        load.subentry_id: HA_GRAPH_PALETTE[index % len(HA_GRAPH_PALETTE)]
        for index, load in enumerate(loads)
    }


@dataclass(frozen=True, slots=True)
class _Site:
    """One site as the views see it: its texts, versions, colours and paths."""

    site: SiteLayout
    texts: Mapping[str, str]
    has: Mapping[str, bool]
    colors: Mapping[str, str]
    several: bool
    subviews: bool
    comma: bool

    def path(self, name: str) -> str:
        return f"{name}-{self.site.entry_id}" if self.several else name

    def subview(self, load: LoadLayout) -> str:
        if self.several:
            return f"appliance-{self.site.entry_id}-{load.subentry_id}".lower()
        return f"appliance-{load.subentry_id}".lower()

    def title(self, text: str) -> str:
        return f"{self.site.name} · {text}" if self.several else text

    def name(self, key: str) -> str:
        """Return the entity's own translated name for a layout key (`ENTITY_NAMES`)."""
        return self.texts[f"entity_{key}"]


def build(
    sites: Sequence[SiteLayout],
    ha_version: str,
    texts: Mapping[str, str],
    *,
    language: str = "en",
    grid_statistics: Sequence[str] = (),
    hidden_views: Collection[str] = (),
    hidden_cards: Collection[str] = (),
) -> dict[str, Any]:
    """Return the dashboard config for `sites` on `ha_version`, every word from `texts`.

    `texts` is `selector.dashboard.options`, plus each `ENTITY_NAMES` entity's
    name as `entity_<key>` and each strategy's words as `strategy_<key>`.
    `language` picks the tables' decimal mark (D12 §5.9). `grid_statistics` are
    the Energy preferences' grid consumption statistics, for History's usage. One site gets the plain
    paths; several get their site's name before each title and its entry id in
    each path, so one dashboard shows every site (D-0439).
    """
    version = AwesomeVersion(ha_version)
    has = {feature: version >= first for feature, first in FEATURES.items()}
    comma = language.split("-", maxsplit=1)[0] in _DECIMAL_COMMA
    views: list[dict[str, Any]] = []
    for layout in sites:
        site = _Site(
            site=layout,
            texts=texts,
            has=has,
            colors=load_colors(layout.loads),
            several=len(sites) > 1,
            subviews="appliances" not in hidden_views,
            comma=comma,
        )
        if "overview" not in hidden_views:
            views.append(_view(site, "overview", _overview(site), hidden_cards))
        if "history" not in hidden_views:
            views.append(_view(site, "history", _history(site, grid_statistics), hidden_cards))
        if site.subviews:
            views.extend(_appliance_view(site, load, hidden_cards) for load in layout.loads)
    return {"title": sites[0].name if len(sites) == 1 else "PowerPlan", "views": views}


def _view(
    site: _Site, name: str, sections: list[Card | None], hidden: Collection[str]
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "title": site.title(site.texts[f"view_{name}"]),
        "path": site.path(name),
        "type": "sections",
        "max_columns": 3,
        "dense_section_placement": True,
        "sections": _kept(sections, hidden),
    }
    e = site.site.entities
    if name == "overview":
        view["header"] = _HEADER
        view["badges"] = [
            badge
            for badge in (
                _badge(e, "active", site.texts["badge_active"], color="amber"),
                _badge(e, "presence", site.texts["badge_presence"]),
                _badge(e, "target", site.texts["badge_target"]),
            )
            if badge is not None
        ]
    if name == "history":
        # Open upward and to the right, as the Energy dashboard's own footer does:
        # downward from the footer the range popover leaves the window (D1).
        picker = {
            "type": "energy-date-selection",
            "collection_key": COLLECTION_KEY,
            "opening_direction": "right",
            "vertical_opening_direction": "up",
        }
        if site.has["footer"]:
            view["footer"] = {"card": picker}
        else:
            view["sections"].insert(0, {"type": "grid", "column_span": 3, "cards": [picker]})
    return view


_HEADER = {"layout": "center", "badges_position": "top", "badges_wrap": "scroll"}


def _kept(sections: list[Card | None], hidden: Collection[str]) -> list[Card]:
    """Return the sections without the card types the strategy hides, and without empty ones."""
    out = []
    for section in sections:
        if section is None:
            continue
        cards = [card for card in section["cards"] if card["type"] not in hidden]
        if all(card["type"] == "heading" for card in cards):
            continue
        out.append({**section, "cards": cards})
    return out


# --------------------------------------------------------------------------- #
# Now
# --------------------------------------------------------------------------- #


def _overview(site: _Site) -> list[Card | None]:
    s, t, e = site.site, site.texts, site.site.entities
    # (F13): one card lists PowerPlan's repairs and explains the meter status, in the
    # household's language; it renders nothing when all is well, so the section needs no condition.
    attention: Card = {"type": ATTENTION_CARD, "grid_options": {"columns": "full", "rows": "auto"}}
    if "meter_health" in e:
        attention["meter_status"] = e["meter_health"]
    sections: list[Card | None] = [
        _grid([attention], span=3),
        # One metric once, and a status only when it needs you: no Effektvarsel tile (the
        # gauge's chip says it when the hour is at risk), no tomorrow badge (the curve's tomorrow
        # half), no planned-energy badge ("Flyttet i tid" in the rail), no step badge (the gauge).
        _section(_heading(t["section_hour"]), _cols(_window_card(site), 12, 6)),
        _section(
            _heading(t["section_price"]),
            _cols(_price_card(site), "full", "auto"),
            span=2,
        ),
        _section(
            _heading(t["section_plan"], _replan_badge(e, t)),
            # The whole house per window, with the rail the appliances card lines up with (F1).
            _cols(_timeline(site, s.loads, hours=24, options=[24, 48], rail=RAIL), "full", "auto"),
            span=3,
        ),
        _section(
            _heading(t["section_appliances"]),
            _cols(_appliances_lanes(site), "full", "auto"),
            None if s.loads else _markdown(t["no_loads"]),
            span=3,
        ),
    ]
    if "metric" in e and "level" in e:
        sections.append(_section(_heading(t["section_capacity"]), _cols(_month_card(site), 12, 6)))
    # (F9): cost and savings so far over daily bars on a fixed 1..N axis - one card for
    # two entity cards (which said "Ukjent") and the statistics graph that labelled days 4:00, 8:00.
    sections.append(
        _section(
            _heading(
                t["section_month"],
                _entity_badge(
                    e,
                    "cost",
                    show_state=False,
                    show_icon=True,
                    icon="mdi:chart-bar",
                    name=t["view_history"],
                    tap_action={
                        "action": "navigate",
                        "navigation_path": f"{DASHBOARD}/{site.path('history')}",
                    },
                ),
            ),
            _cols(_month_bars(e), 12, 5),
        )
    )
    if s.has_production:
        sections.append(
            _section(
                _heading(t["section_solar"]),
                _tile(e, "production", site.name("production"), {"type": "trend-graph"}),
                _tile(e, "surplus", site.name("surplus"), {"type": "trend-graph"}),
            )
        )
    return sections


def _history(site: _Site, grid: Sequence[str]) -> list[Card | None]:
    s, t, e = site.site, site.texts, site.site.entities
    events = [e["events"]] if "events" in e else []
    events += [load.entities["plan_status"] for load in s.loads if "plan_status" in load.entities]
    return [
        _section(
            _heading(t["section_summary"]),
            _cols(_summary_card(site, grid), "full", 2),
            span=3,
        ),
        _section(
            _heading(
                t["section_usage"],
                {
                    "type": "button",
                    "icon": "mdi:arrow-top-right",
                    "tap_action": {"action": "navigate", "navigation_path": "/energy"},
                },
            ),
            _cols(_history_timeline(site, grid), "full", "auto"),
            span=2,
        )
        if grid
        else None,
        _section(
            _heading(t["section_capacity"]),
            _cols(_peaks_card(site, grid), 12, "auto"),
        ),
        # The table only, full width; the saving bars were its Spart column drawn again,
        # and the cost-and-savings graph is Forbruk × price, with the day's total in Oppsummering.
        _section(
            _heading(t["section_cost_per_appliance"]),
            _cols(_appliances_card(site, "table", "cost_month"), "full", "auto"),
            span=3,
        ),
        _section(
            _heading(t["section_events"]),
            _cols(
                {"type": "logbook", "target": {"entity_id": events}, "hours_to_show": 48},
                12,
                4,
            )
            if events
            else None,
        ),
    ]


# --------------------------------------------------------------------------- #
# An appliance's page
# --------------------------------------------------------------------------- #


def _appliance_view(site: _Site, load: LoadLayout, hidden: Collection[str]) -> dict[str, Any]:
    t, e, color = site.texts, load.entities, site.colors[load.subentry_id]
    badges = [
        _badge(e, "control", t["badge_control"], color=color),
        _badge(e, "plan_status", t["badge_status"], color=color),
        _badge(e, "ready_by", t["badge_ready_by"], color=color),
        *_next_run_badges(e, t, color),
    ]
    status = _tile(e, "plan_status", t["card_status"], color=color)
    if status is not None:
        # The reason in the household's words: HA translates the attribute (B7, D-0481).
        status["state_content"] = ["state", "reason_key"]
    granted = _tile(e, "granted_power", site.name("granted_power"), {"type": "trend-graph"})
    sections: list[Card | None] = [
        _section(
            _heading(t["section_control"]),
            _cols(
                _tile(
                    e,
                    "control",
                    t["card_control"],
                    {"type": "select-options"},
                    color=color,
                    inline=True,
                ),
                12,
                1,
            ),
            _cols(status, 12, 1),
            *_controls(site, load),
            _cols(
                {
                    "type": "entities",
                    # An explicit icon: the row's picture would be the entity's own (A2).
                    "entities": [
                        {
                            "entity": e["ready_by"],
                            "name": site.name("ready_by"),
                            "icon": "mdi:clock-check-outline",
                        }
                    ],
                },
                12,
                "auto",
            )
            if "ready_by" in e
            else None,
            _cols(granted, 12, 2),
        ),
        _section(
            _heading(t["section_appliance_plan"]),
            _cols(
                _timeline(
                    site, (load,), hours=24, options=[12, 24, 48], deadline=e.get("plan_status")
                ),
                "full",
                "auto",
            ),
            span=2,
        ),
        _section(
            _heading(t["section_why"]),
            _cols(_markdown(why(site, load)), "full", "auto")
            if "plan_status" in e and "plan" in site.site.entities
            else None,
            span=2,
        ),
        _section(
            _heading(t["section_month"]),
            # The month's money is the sensors' own state; `energy` is a
            # `total_increasing` counter, so its month is a statistic (A5, N7).
            _cols(_entity(e, "cost_month", t["card_cost"], "mdi:cash"), 6, 2),
            _cols(_entity(e, "savings_month", t["card_savings"], "mdi:piggy-bank"), 6, 2),
            _cols(_statistic(e, "energy", t["card_energy"]), 12, 2),
        ),
    ]
    return {
        "title": load.name,
        "path": site.subview(load),
        "icon": load.icon,
        "subview": True,
        "back_path": f"{DASHBOARD}/{site.path('overview')}",
        "type": "sections",
        "max_columns": 3,
        "dense_section_placement": True,
        "header": _HEADER,
        "badges": [badge for badge in badges if badge is not None],
        "sections": _kept(sections, hidden),
    }


def _controls(site: _Site, load: LoadLayout) -> list[Card | None]:
    """Return one appliance's own controls by type (D12 §5.1's table, D8 §5.16's entity set)."""
    e, kind = load.entities, load.type
    slider = {"type": "numeric-input", "style": "slider"}
    buttons = {"type": "numeric-input", "style": "buttons"}
    cards: list[Card | None] = []
    if kind in {"ev", "battery"}:
        cards.append(_cols(_tile(e, "charge_target", site.name("charge_target"), slider), 12, 2))
    if kind == "ev":
        cards.append(_cols(_tile(e, "charge_min", site.name("charge_min"), slider), 12, 2))
    if kind == "battery":
        soc = _tile(e, "soc", site.texts["card_soc"], {"type": "bar-gauge", "min": 0, "max": 100})
        cards.append(_cols(soc, 12, 2))
    if kind in _THERMAL:
        cards.extend(
            (
                _cols(_tile(e, "comfort", site.name("comfort"), buttons, inline=True), 12, 1),
                _cols(
                    _tile(
                        e,
                        "follow_presence",
                        site.name("follow_presence"),
                        {"type": "toggle"},
                        inline=True,
                    ),
                    12,
                    1,
                ),
            )
        )
    if kind == "water_heater":
        cards.append(_cols(_tile(e, "next_legionella", site.name("next_legionella")), 12, 1))
    if kind == "appliance_cycle":
        cards.append(
            _cols(_tile(e, "run_now", site.name("run_now"), {"type": "button"}, inline=True), 12, 1)
        )
    if kind == "generic_switch":
        cards.append(
            _cols(
                _tile(e, "hours_per_day", site.name("hours_per_day"), buttons, inline=True), 12, 1
            )
        )
    return cards


# --------------------------------------------------------------------------- #
# The markdown card: why this plan (D12 §5.9)
# --------------------------------------------------------------------------- #


def _lit(value: object) -> str:
    """Return `value` as a Jinja literal: JSON is one (strings, numbers, lists, dicts)."""
    return json.dumps(value, ensure_ascii=False)


def _quoted(text: str) -> str:
    """Return `text` safe inside a single-quoted Jinja string."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _currency_word(site: _Site) -> str:
    """Return the site's currency as a markdown card writes it: `currency_short` for NOK ("kr"), else the code (G7)."""
    return site.texts["currency_short"] if site.site.currency == "NOK" else site.site.currency


def _number_macros(site: _Site) -> str:
    """Two macros: `n(x)`, two decimals in the language's mark, and `sn(x)`, signed."""
    if site.comma:
        return (
            "{%- macro n(x) -%}{{ ('%.2f' | format(x)) | replace('.', ',') }}{%- endmacro -%}\n"
            "{%- macro sn(x) -%}{{ ('%+.2f' | format(x)) | replace('.', ',') | replace('-', '−') }}"
            "{%- endmacro -%}\n"
        )
    return (
        "{%- macro n(x) -%}{{ '%.2f' | format(x) }}{%- endmacro -%}\n"
        "{%- macro sn(x) -%}{{ '%+.2f' | format(x) }}{%- endmacro -%}\n"
    )


def why(site: _Site, load: LoadLayout) -> str:
    """Return an appliance's "why this plan?": its need, the chosen runs, coverage and cost."""
    t = site.texts
    strategies = {
        key.removeprefix("strategy_"): value
        for key, value in t.items()
        if key.startswith("strategy_")
    }
    confidence = {key: t[f"confidence_{key}"] for key in _CONFIDENCE}
    before = t["md_before"].replace(
        "{time}", "' ~ (a.deadline | as_timestamp | timestamp_custom(hm)) ~ '"
    )
    return (
        f"{{%- set e = {_lit(load.entities['plan_status'])} -%}}\n"
        f"{{%- set id = {_lit(load.subentry_id)} -%}}\n"
        "{%- set a = states[e].attributes if states[e] is defined else {} -%}\n"
        f"{{%- set slots = state_attr({_lit(site.site.entities['plan'])}, 'slots') or [] -%}}\n"
        + _number_macros(site)
        + "{%- set ns = namespace(runs=[], cs=none, ce=none) -%}\n"
        "{%- for s in slots if (s.planned_kwh or {}).get(id, 0) > 0 -%}\n"
        "{%- if ns.ce == s.start -%}{%- set ns.ce = s.end -%}\n"
        "{%- else -%}\n"
        "{%- if ns.cs -%}{%- set ns.runs = ns.runs + [[ns.cs, ns.ce]] -%}{%- endif -%}\n"
        "{%- set ns.cs = s.start -%}{%- set ns.ce = s.end -%}\n"
        "{%- endif -%}\n"
        "{%- endfor -%}\n"
        "{%- if ns.cs -%}{%- set ns.runs = ns.runs + [[ns.cs, ns.ce]] -%}{%- endif -%}\n"
        "{%- set hm = '%H:%M' -%}\n"
        f"{{%- set strategy = {_lit(strategies)} -%}}\n"
        f"{{%- set conf = {_lit(confidence)} -%}}\n"
        f'<ha-icon icon="mdi:clock-check-outline"></ha-icon> **{t["md_need"]}:** '
        "{{ n(a.planned_kwh | float(0)) }} kWh"
        f"{{{{ (' ' ~ '{before}') if a.deadline }}}}\n\n"
        f'<ha-icon icon="mdi:weather-night"></ha-icon> **{t["md_chosen"]}:** '
        "{% for s, en in ns.runs %}{{ s | as_timestamp | timestamp_custom(hm) }}–"
        "{{ en | as_timestamp | timestamp_custom(hm) }}{{ ', ' if not loop.last }}"
        f"{{% else %}}{t['md_none_planned']}{{% endfor %}}\n\n"
        f'<ha-icon icon="mdi:check-circle-outline"></ha-icon> **{t["md_coverage"]}:** '
        "{{ ((a.coverage | float(0)) * 100) | round(0) | int }} % · "
        "{{ conf.get(a.confidence, a.confidence) }}\n\n"
        f'<ha-icon icon="mdi:cash"></ha-icon> **{t["md_cost_est"]}:** '
        "≈ {{ n((a.cost or '0').split(' ')[0] | float(0)) }} "
        f"{_currency_word(site)} · {{{{ strategy.get(a.strategy, a.strategy) }}}}"
    )


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #


def _grid(cards: list[Card], *, span: int | None = None) -> Card | None:
    if not cards:
        return None
    section: Card = {"type": "grid", "cards": cards}
    if span is not None:
        section["column_span"] = span
    return section


def _section(heading: Card, *cards: Card | None, span: int | None = None) -> Card | None:
    """Return a grid section under a heading, `None` when no card is left.

    `column_span` sits on the section, beside `type` and `cards` - never on a
    card, where HA ignores it (B2).
    """
    kept = [card for card in cards if card is not None]
    if not kept:
        return None
    return _grid([heading, *kept], span=span)


def _heading(text: str, *badges: Card | None) -> Card:
    card: Card = {"type": "heading", "heading": text}
    kept = [badge for badge in badges if badge is not None]
    if kept:
        card["badges"] = kept
    return card


def _cols(card: Card | None, columns: int | str, rows: int | str | None = None) -> Card | None:
    """Return `card` with its `grid_options`; `rows: "auto"` where its content is shorter (G6)."""
    if card is None:
        return None
    options: dict[str, int | str] = {"columns": columns}
    if rows is not None:
        options["rows"] = rows
    return {**card, "grid_options": options}


def _badge(
    entities: Mapping[str, str],
    key: str,
    name: str,
    *,
    color: str | None = None,
    state_content: list[str] | None = None,
) -> Card | None:
    """Return a view badge: the entity's name and state; a tap opens more-info."""
    if key not in entities:
        return None
    badge: Card = {
        "type": "entity",
        "entity": entities[key],
        "name": name,
        "show_name": True,
        "show_state": True,
    }
    if color is not None:
        badge["color"] = color
    if state_content is not None:
        badge["state_content"] = state_content
    return badge


def _entity_badge(entities: Mapping[str, str], key: str, **options: Any) -> Card | None:
    if key not in entities:
        return None
    return {"type": "entity", "entity": entities[key], **options}


def _replan_badge(entities: Mapping[str, str], texts: Mapping[str, str]) -> Card | None:
    """Return "replan" as a heading badge that presses the site's own button (D12 §5.4)."""
    if "replan" not in entities:
        return None
    button = entities["replan"]
    return {
        "type": "entity",
        "entity": button,
        "name": texts["badge_replan"],
        "show_name": True,
        "show_state": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": "button.press",
            "target": {"entity_id": button},
        },
    }


def _next_run_badges(
    entities: Mapping[str, str], texts: Mapping[str, str], color: str
) -> list[Card | None]:
    """Return the subview's "next run" badge: the time, or "running now" while it runs (A1).

    `next_run` is empty while a run is in progress, so the time badge hides then
    and a second badge, named `badge_running_now`, shows instead.
    """
    time = _badge(
        entities, "plan_status", texts["badge_next_run"], color=color, state_content=["next_run"]
    )
    running = _badge(entities, "plan_status", texts["badge_running_now"], color=color)
    if time is None or running is None:
        return []
    when = {"condition": "state", "entity": entities["plan_status"]}
    time["visibility"] = [{**when, "state_not": list(RUNNING)}]
    running["show_state"] = False
    running["visibility"] = [{**when, "state": list(RUNNING)}]
    return [time, running]


def _tile(
    entities: Mapping[str, str],
    key: str,
    name: str,
    *features: Card,
    icon: str | None = None,
    color: str | None = None,
    inline: bool = False,
) -> Card | None:
    if key not in entities:
        return None
    card: Card = {"type": "tile", "entity": entities[key], "name": name}
    if icon is not None:
        card["icon"] = icon
    if color is not None:
        card["color"] = color
    if features:
        card["features"] = list(features)
        if inline:
            card["features_position"] = "inline"
    return card


def _entity(entities: Mapping[str, str], key: str, name: str, icon: str) -> Card | None:
    """Return an `entity` card: a month-to-date sensor's own state (N7, A5).

    A `statistic` card's change of a monetary total doubled a month's
    423,32 NOK; the sensors already are the month so far, so the state is the truth.
    """
    if key not in entities:
        return None
    return {"type": "entity", "entity": entities[key], "name": name, "icon": icon}


def _statistic(
    entities: Mapping[str, str],
    key: str,
    name: str,
    icon: str | None = None,
) -> Card | None:
    """Return this calendar month's change of a `total_increasing` counter (D8 §5.5)."""
    if key not in entities:
        return None
    card: Card = {
        "type": "statistic",
        "entity": entities[key],
        "name": name,
        "stat_type": "change",
        "period": {"calendar": {"period": "month"}},
    }
    if icon is not None:
        card["icon"] = icon
    return card


def _markdown(content: str) -> Card:
    return {"type": "markdown", "content": content}


def _timeline(
    site: _Site,
    loads: Sequence[LoadLayout],
    *,
    hours: int,
    options: list[int],
    deadline: str | None = None,
    rail: int | None = None,
) -> Card | None:
    """Return the timeline (D12 §5.2): the plan's slots against the price forecast.

    With `rail`, Now's whole-house forecast per window beside a rail that wide (§5.12 F1).
    """
    e = site.site.entities
    if "plan" not in e or "price_forecast" not in e:
        return None
    entities = {"plan": e["plan"], "price_forecast": e["price_forecast"]}
    if deadline is not None:
        entities["deadline"] = deadline
        show = ["plan", "price"]
    elif rail is not None:
        # (F3) The whole house per window, "Kan bli opptil" as a cap on each bar.
        # No price track; Strømpris sits right above.
        show = ["plan", "baseline", "reserve", "ceiling"]
    else:
        show = ["plan", "baseline", "ceiling", "price"]
        if site.site.has_production:
            show.append("production")
    card: Card = {
        "type": TIMELINE_CARD,
        "entry_id": site.site.entry_id,
        "hours": hours,
        "hours_options": options,
        "loads": [
            {"id": load.subentry_id, "name": load.name, "color": site.colors[load.subentry_id]}
            for load in loads
        ],
        "entities": entities,
        "show": show,
        "currency": site.site.currency,
        "labels": _labels(site.texts),
    }
    if deadline is None:
        card["narrow_hours"] = 12
    if rail is not None:
        card["rail_width"] = rail
        card["bucket"] = "window"  # aggregate slots to window_min (60) → kWh/t
    return card


def _price_card(site: _Site) -> Card | None:
    """Return the price now, today and tomorrow, and the fixed price's effect (D12 §5.12 P1–P5)."""
    e = site.site.entities
    if "price" not in e or "price_forecast" not in e:
        return None
    # (F5) The price card keeps its own words; the spot is on `price_forecast`, the
    # month's effect is `fixed_price_savings`, the retry is a button (D12 §5.16 R2, R3). No
    # tomorrow or capacity step: the curve and Effekttrinn say them.
    names = {
        "price": "price",
        "price_forecast": "price_forecast",
        "fixed_price_savings": "fixed_price_savings",
        "refresh": "refresh_prices",
    }
    return {
        "type": PRICE_CARD,
        "entry_id": site.site.entry_id,
        "entities": {key: e[source] for key, source in names.items() if source in e},
    }


#: An appliance's entities the appliances card and its dialog read, by the card's own names (§5.15 F2).
_LANE_KEYS = {
    "control": "control",
    "deadline": "ready_by",
    "cost": "cost_month",
    "savings": "savings_month",
    "energy": "energy",
    "legionella": "next_legionella",
}


def _appliances_lanes(site: _Site) -> Card | None:
    """Return one row per appliance with its next 24 h as a lane (D12 §5.12 R1–R7).

    It stands in for v0.5's tiles, power split and runs list. A row opens the
    appliance's dialog, whose "open details" goes to the subview while there are
    subviews.
    """
    e = site.site.entities
    if "plan" not in e:
        return None
    loads = []
    for load in site.site.loads:
        if "plan_status" not in load.entities:
            continue
        row: Card = {
            "id": load.subentry_id,
            "name": load.name,
            "color": site.colors[load.subentry_id],
            "icon": load.icon,
            "kind": load.type,
            "status": load.entities["plan_status"],
            **{key: load.entities[src] for key, src in _LANE_KEYS.items() if src in load.entities},
        }
        if load.area:
            row["area"] = load.area
        if site.subviews:
            row["path"] = f"{DASHBOARD}/{site.subview(load)}"
        loads.append(row)
    if not loads:
        return None
    entities = {"plan": e["plan"]}
    if "price_forecast" in e:
        entities["price_forecast"] = e["price_forecast"]
    return {
        "type": APPLIANCES_CARD,
        "entry_id": site.site.entry_id,
        "entities": entities,
        "loads": loads,
        "hours": 24,
        "rail_width": RAIL,
        "currency": site.site.currency,
    }


def _month_bars(e: Mapping[str, str]) -> Card | None:
    """Return the month's cost and savings over the cost per day, 1..N (D12 §5.15 F9)."""
    if "cost" not in e:
        return None
    card: Card = {"type": MONTH_BARS, "entity": e["cost"]}
    if "savings" in e:
        card["savings"] = e["savings"]
    return card


def _window_card(site: _Site) -> Card | None:
    """Return the window gauge (D12 §5.3), `mode: hour`, on the rows the site shows."""
    e = site.site.entities
    if "window_used" not in e:
        return None
    keys = (
        "window_used",
        "window_projected",
        "ceiling",
        "allowance",
        "stage",
        "peak_warning",
        "next_peak_warning",
    )
    return {
        "type": WINDOW_CARD,
        "entry_id": site.site.entry_id,
        "mode": "hour",
        "entities": {key: e[key] for key in keys if key in e},
        "labels": _labels(site.texts),
    }


def _summary_card(site: _Site, grid: Sequence[str]) -> Card:
    """Return History's four figures over the picker's period (D12 §5.7)."""
    e = site.site.entities
    keys = ("cost", "savings", "metric", "level", "window_used", "advice")
    return {
        "type": SUMMARY_CARD,
        "entry_id": site.site.entry_id,
        "entities": {key: e[key] for key in keys if key in e},
        "grid_entities": list(grid),
        "labels": _labels(site.texts),
    }


def _history_timeline(site: _Site, grid: Sequence[str]) -> Card:
    """Return the timeline over the past: grid usage against the limit (D12 §5.7)."""
    e = site.site.entities
    return {
        "type": TIMELINE_CARD,
        "entry_id": site.site.entry_id,
        "mode": "history",
        "grid_entities": list(grid),
        "entities": {
            key: e[key]
            for key in ("window_used", "ceiling", "price", "price_forecast", "advice")
            if key in e
        },
        "currency": site.site.currency,
        "labels": _labels(site.texts),
    }


def _peaks_card(site: _Site, grid: Sequence[str]) -> Card | None:
    """Return each day's highest hour over the picker's period (D12 §5.7, `mode: peaks`).

    `grid_entities` stand in for `window_used` on days before its statistics (D4).
    """
    e = site.site.entities
    if "window_used" not in e:
        return None
    return {
        "type": WINDOW_CARD,
        "entry_id": site.site.entry_id,
        "mode": "peaks",
        "entities": {
            key: e[key]
            for key in ("window_used", "ceiling", "advice", "level", "target")
            if key in e
        },
        "grid_entities": list(grid),
        "labels": _labels(site.texts),
    }


def _appliances_card(site: _Site, view: str, key: str) -> Card | None:
    """Return the period summary's per-appliance `view` over the picker's period (D5, D6).

    `appliances`: each load's saving as a diverging bar; `table`: cost and saving
    by appliance with a sum. Either follows the picker; neither has a title.
    """
    loads = [
        {
            "id": load.subentry_id,
            "name": load.name,
            "color": site.colors[load.subentry_id],
            **{k: load.entities[k] for k in ("cost_month", "savings_month") if k in load.entities},
        }
        for load in site.site.loads
        if key in load.entities
    ]
    if not loads:
        return None
    return {
        "type": SUMMARY_CARD,
        "entry_id": site.site.entry_id,
        "view": view,
        "loads": loads,
        "currency": site.site.currency,
        "labels": _labels(site.texts),
    }


def _month_card(site: _Site) -> Card:
    """Return the capacity step's gauge (D12 §5.3, `mode: month`); the site shows `metric`."""
    e = site.site.entities
    keys = ("metric", "level", "projected_level", "advice", "target")
    return {
        "type": WINDOW_CARD,
        "entry_id": site.site.entry_id,
        "mode": "month",
        "entities": {key: e[key] for key in keys if key in e},
        "labels": _labels(site.texts),
    }


def _labels(texts: Mapping[str, str]) -> dict[str, str]:
    """Return the custom cards' own words, from the integration's translations (D-0446)."""
    return {
        key.removeprefix("card_"): value
        for key, value in texts.items()
        if key.startswith(("card_", "summary_"))
    }
