"""The dashboard's layout (D12 §5.1): `SiteLayout`s in, a Lovelace config out.

Two text tabs - `overview` (now) and `history` (the past, on the Energy
dashboard's own period picker, `energy_powerplan`) - and one subview per
appliance, each a `sections` view with `max_columns: 3` and dense placement.
Section order is the phone's reading order. Built-in cards wherever one can
show the thing; the timeline and the window gauge are the custom cards (D12
§5.2, §5.3), and three markdown tables carry what only a table can (§5.9).
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
CUSTOM_CARDS = frozenset({TIMELINE_CARD, WINDOW_CARD})
#: The keys `hidden_views` takes: the two tabs, and every appliance's subview.
VIEWS: tuple[str, ...] = ("overview", "history", "appliances")
#: The dashboard's own URL path; `strategy.ts` puts it in after the call (D12 §5.1).
DASHBOARD = "{dashboard}"
#: The first HA release with each card or view feature (D12 §5.5): the frontend
#: build each release pins, read at its tag (20260128.6, 20260304.0).
FEATURES: Mapping[str, AwesomeVersion] = {
    "distribution": AwesomeVersion("2026.2.0"),
    "repairs": AwesomeVersion("2026.3.0"),
    "footer": AwesomeVersion("2026.3.0"),
    "entity_color": AwesomeVersion("2026.6.0"),
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
    "level": ("sensor", "level"),
    "metric": ("sensor", "metric"),
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
    has_energy_grid: bool = False,
    hidden_views: Collection[str] = (),
    hidden_cards: Collection[str] = (),
) -> dict[str, Any]:
    """Return the dashboard config for `sites` on `ha_version`, every word from `texts`.

    `texts` is `selector.dashboard.options`, plus each `ENTITY_NAMES` entity's
    name as `entity_<key>` and each strategy's words as `strategy_<key>`.
    `language` picks the tables' decimal mark (D12 §5.9). One site gets the plain
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
            views.append(
                _view(
                    site, "history", _history(site, has_energy_grid=has_energy_grid), hidden_cards
                )
            )
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
        picker = {"type": "energy-date-selection", "collection_key": COLLECTION_KEY}
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
    meter = _tile(
        e, "meter_health", t["card_meter_status"], icon="mdi:meter-electric", color="orange"
    )
    if meter is not None:
        meter["visibility"] = [
            {"condition": "state", "entity": e["meter_health"], "state_not": "ok"}
        ]
    repairs = {"type": "repairs", "hide_empty": True} if site.has["repairs"] else None
    # No heading: a section whose cards all hide hides itself (HA 2026.9's grid section).
    attention = [card for card in (_cols(repairs, 12), _cols(meter, 12)) if card is not None]
    sections: list[Card | None] = [
        _grid(attention, span=3),
        _section(
            _heading(t["section_hour"]),
            _cols(_window_card(site), 12, 6),
            _cols(
                _tile(e, "peak_warning", t["card_peak_warning"], icon="mdi:alert-outline"),
                12,
                1,
            ),
        ),
        _section(
            _heading(
                t["section_plan"],
                _entity_badge(e, "plan", show_state=True, show_icon=True),
                _replan_badge(e, t),
            ),
            _cols(_timeline(site, s.loads, hours=24, options=[24, 48]), "full", 7),
            span=3,
        ),
        _section(
            _heading(t["section_appliances"]),
            _power_split(site),
            *(_appliance_tile(site, load) for load in s.loads),
            None if s.loads else _markdown(t["no_loads"]),
            span=2,
        ),
    ]
    if "metric" in e and "level" in e:
        sections.append(
            _section(
                _heading(
                    t["section_capacity"],
                    _entity_badge(e, "projected_level", show_state=True, icon="mdi:stairs"),
                ),
                _cols(_month_card(site), 12, 6),
            )
        )
    sections.extend(
        (
            _section(
                _heading(
                    t["section_month"],
                    {
                        "type": "button",
                        "icon": "mdi:chart-bar",
                        "tap_action": {
                            "action": "navigate",
                            "navigation_path": f"{DASHBOARD}/{site.path('history')}",
                        },
                    },
                ),
                _cols(_statistic(e, "cost", t["card_cost"], "mdi:cash"), 6, 2),
                _cols(_statistic(e, "savings", t["card_savings"], "mdi:piggy-bank"), 6, 2),
                _cols(
                    _tile(e, "plan", t["card_planned"], icon="mdi:calendar-clock", color="primary"),
                    12,
                    1,
                ),
                _cols(
                    _tile(
                        e,
                        "price",
                        t["card_price_now"],
                        {"type": "trend-graph", "hours_to_show": 24},
                        color="primary",
                    ),
                    12,
                    2,
                ),
                _cols(
                    _tile(e, "prices_tomorrow", t["card_prices_tomorrow"], color="primary"), 12, 1
                ),
            ),
            _section(
                _heading(t["section_next_runs"]),
                _cols(_markdown(next_runs(site)), 12, 6) if "plan" in e else None,
            ),
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


def _power_split(site: _Site) -> Card | None:
    """Return who is granted what now, where the household enabled `granted_power` (D-0438)."""
    rows = []
    for load in site.site.loads:
        if "granted_power" not in load.entities:
            continue
        row: Card = {"entity": load.entities["granted_power"], "name": load.name}
        if site.has["distribution"]:
            row["color"] = site.colors[load.subentry_id]
        rows.append(row)
    if not rows:
        return None
    if not site.has["distribution"]:
        return _cols({"type": "entities", "entities": rows}, "full")
    card = {"type": "distribution", "title": site.texts["card_power_split"], "entities": rows}
    return _cols(card, "full", 2)


def _appliance_tile(site: _Site, load: LoadLayout) -> Card | None:
    """Return the appliance's tile on Now: its status, its colour, a tap to its page."""
    e = load.entities
    if "plan_status" not in e:
        return None
    card: Card = {
        "type": "tile",
        "entity": e["plan_status"],
        "name": load.name,
        "color": site.colors[load.subentry_id],
        "state_content": ["state", "deadline" if load.type == "ev" else "next_start"],
        "hold_action": {"action": "more-info"},
    }
    if site.subviews:
        card["tap_action"] = {
            "action": "navigate",
            "navigation_path": f"{DASHBOARD}/{site.subview(load)}",
        }
    else:
        card["tap_action"] = {"action": "more-info"}
    return _cols(card, 12, 1)


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


def _history(site: _Site, *, has_energy_grid: bool) -> list[Card | None]:
    s, t, e = site.site, site.texts, site.site.entities
    savings = [
        _graph_entity(
            site, load.entities["savings_month"], load.name, site.colors[load.subentry_id]
        )
        for load in s.loads
        if "savings_month" in load.entities
    ]
    costs = [
        (load.name, load.entities["cost_month"], load.entities.get("savings_month"))
        for load in s.loads
        if "cost_month" in load.entities
    ]
    events = [e["events"]] if "events" in e else []
    events += [load.entities["plan_status"] for load in s.loads if "plan_status" in load.entities]
    return [
        _section(
            _heading(t["section_summary"]),
            _cols(_statistic(e, "cost", t["card_cost"], "mdi:cash"), 9, 2),
            _cols(_statistic(e, "savings", t["card_savings"], "mdi:piggy-bank"), 9, 2),
            _cols(_statistic(e, "metric", site.name("metric"), "mdi:flash", stat_type="max"), 9, 2),
            _cols(_tile(e, "level", site.name("level")), 9, 2),
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
            _cols({"type": "energy-usage-graph", "collection_key": COLLECTION_KEY}, "full", 6),
            span=2,
        )
        if has_energy_grid
        else None,
        _section(
            _heading(t["section_capacity"]),
            _cols(
                _graph(
                    [e["window_used"]] if "window_used" in e else [],
                    ["max"],
                    "bar",
                    title=t["card_peak_hour"],
                ),
                12,
                6,
            ),
        ),
        _section(
            _heading(t["section_per_appliance"]),
            _cols(
                _graph(
                    savings,
                    ["change"],
                    "bar",
                    title=t["card_savings_per_appliance"],
                    period="month",
                ),
                "full",
                6,
            ),
            span=2,
        ),
        _section(
            _heading(
                t["section_cost_per_appliance"],
                {"type": "button", "text": t["badge_this_month"], "tap_action": {"action": "none"}},
            ),
            _cols(_markdown(cost_per_appliance(site, costs)), 12, 6) if costs else None,
        ),
        _section(
            _heading(t["section_cost_savings"]),
            _cols(
                _graph(
                    [
                        _graph_entity(site, e[key], None, color)
                        for key, color in (("cost", "primary"), ("savings", "green"))
                        if key in e
                    ],
                    ["change"],
                    "bar",
                ),
                "full",
                4,
            ),
            span=2,
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
        _badge(e, "plan_status", t["badge_next_run"], color=color, state_content=["next_start"]),
    ]
    status = _tile(e, "plan_status", t["card_status"], color=color)
    if status is not None:
        status["state_content"] = ["state"]
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
                    "entities": [{"entity": e["ready_by"], "name": site.name("ready_by")}],
                },
                12,
                1,
            )
            if "ready_by" in e
            else None,
            _cols(granted, 12, 2),
        ),
        _section(
            _heading(
                t["section_appliance_plan"],
                _entity_badge(e, "plan_status", state_content=["planned_kwh"]),
            ),
            _cols(
                _timeline(
                    site, (load,), hours=24, options=[12, 24, 48], deadline=e.get("plan_status")
                ),
                "full",
                5,
            ),
            span=2,
        ),
        _section(
            _heading(t["section_why"]),
            _cols(_markdown(why(site, load)), "full", 4)
            if "plan_status" in e and "plan" in site.site.entities
            else None,
            span=2,
        ),
        _section(
            _heading(t["section_month"]),
            _cols(_statistic(e, "cost_month", t["card_cost"]), 6, 2),
            _cols(_statistic(e, "savings_month", t["card_savings"]), 6, 2),
            _cols(_statistic(e, "energy", t["card_energy"]), 6, 2),
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
# The markdown tables (D12 §5.9)
# --------------------------------------------------------------------------- #


def _lit(value: object) -> str:
    """Return `value` as a Jinja literal: JSON is one (strings, numbers, lists, dicts)."""
    return json.dumps(value, ensure_ascii=False)


def _quoted(text: str) -> str:
    """Return `text` safe inside a single-quoted Jinja string."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _cell(name: str) -> str:
    """Return a name that cannot break a markdown table row."""
    return name.replace("|", "\\|")


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


def next_runs(site: _Site) -> str:
    """Return the Now view's "next runs" table: each appliance's next start, kWh and cost."""
    s, t = site.site, site.texts
    names = {load.subentry_id: _cell(load.name) for load in s.loads}
    live = [
        [load.entities["plan_status"], load.name]
        for load in s.loads
        if "plan_status" in load.entities
    ]
    days = t["md_weekdays"].split(",")
    running = t["md_running_now"].replace("{name}", "{{ n_ }}")
    deadline = t["md_deadline_short"].replace(
        "{time}", "' ~ (d_ | as_timestamp | timestamp_custom('%H:%M')) ~ '"
    )
    return (
        f"{{%- set by = state_attr({_lit(s.entities['plan'])}, 'by_load') or {{}} -%}}\n"
        f"{{%- set names = {_lit(names)} -%}}\n"
        f"{{%- set days = {_lit(days)} -%}}\n"
        + _number_macros(site)
        + "{%- set ns = namespace(rows=[], kwh=0, cost=0) -%}\n"
        "{%- for id, l in by.items() if (l.planned_kwh or 0) >= 0.05 and l.next_start -%}\n"
        "{%- set c = (l.cost or '0').split(' ')[0] | float(0) -%}\n"
        "{%- set ns.rows = ns.rows + [[as_timestamp(l.next_start), names.get(id, id), l.planned_kwh, c]] -%}\n"
        "{%- set ns.kwh = ns.kwh + l.planned_kwh -%}{%- set ns.cost = ns.cost + c -%}\n"
        "{%- endfor -%}\n"
        "{%- if ns.rows %}\n"
        f"| {t['md_start']} | {t['md_appliance']} | kWh | {s.currency} |\n"
        "|:--|:--|--:|--:|\n"
        "{% for ts, name, kwh, cost in ns.rows | sort(attribute='0') -%}\n"
        "| {{ (days[(ts | timestamp_custom('%w') | int + 6) % 7] ~ ' ') "
        "if (ts | timestamp_custom('%Y-%m-%d')) != now().strftime('%Y-%m-%d') }}"
        "{{ ts | timestamp_custom('%H:%M') }} | {{ name }} | {{ n(kwh) }} | {{ n(cost) }} |\n"
        "{% endfor -%}\n"
        f"| **{t['md_total']}** | | **{{{{ n(ns.kwh) }}}}** | **{{{{ n(ns.cost) }}}}** |\n\n"
        f"_{t['md_plan_horizon']}_\n"
        "{%- else %}\n"
        f"{t['md_no_runs']}\n"
        "{%- endif %}\n"
        f"{{%- for e_, n_ in {_lit(live)} if states(e_) in {_lit(list(RUNNING))} %}}\n"
        "{%- set d_ = state_attr(e_, 'deadline') %}\n\n"
        f'<ha-icon icon="mdi:flash"></ha-icon> {running}'
        f"{{{{ (' · ' ~ '{deadline}') if d_ }}}}\n"
        "{%- endfor %}"
    )


def cost_per_appliance(site: _Site, rows: list[tuple[str, str, str | None]]) -> str:
    """Return History's "cost per appliance" table: this month's cost and saving, dearest first."""
    t = site.texts
    data = [[_cell(name), cost, savings or ""] for name, cost, savings in rows]
    return (
        f"{{%- set rows = {_lit(data)} -%}}\n"
        + _number_macros(site)
        + "{%- set ns = namespace(items=[], c=0, s=0) -%}\n"
        "{%- for name, ce, se in rows -%}\n"
        "{%- set c = states(ce) | float(0) -%}{%- set s = (states(se) | float(0)) if se else 0 -%}\n"
        "{%- set ns.items = ns.items + [[c, name, s]] -%}{%- set ns.c = ns.c + c -%}{%- set ns.s = ns.s + s -%}\n"
        "{%- endfor %}\n"
        f"| {t['md_appliance']} | {t['card_cost']} | {t['md_saved']} |\n"
        "|:--|--:|--:|\n"
        "{% for c, name, s in ns.items | sort(attribute='0', reverse=true) -%}\n"
        "| {{ name }} | {{ n(c) }} | {{ sn(s) }} |\n"
        "{% endfor -%}\n"
        f"| **{t['md_total']}** | **{{{{ n(ns.c) }}}}** | **{{{{ sn(ns.s) }}}}** |\n\n"
        f"_{t['md_negative_saving']}_"
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
        f"{site.site.currency} · {{{{ strategy.get(a.strategy, a.strategy) }}}}"
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


def _cols(card: Card | None, columns: int | str, rows: int | None = None) -> Card | None:
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


def _statistic(
    entities: Mapping[str, str],
    key: str,
    name: str,
    icon: str | None = None,
    *,
    stat_type: str = "change",
) -> Card | None:
    """Return this calendar month's change of a monetary total (D8 §5.5), or another stat."""
    if key not in entities:
        return None
    card: Card = {
        "type": "statistic",
        "entity": entities[key],
        "name": name,
        "stat_type": stat_type,
        "period": {"calendar": {"period": "month"}},
    }
    if icon is not None:
        card["icon"] = icon
    return card


def _markdown(content: str) -> Card:
    return {"type": "markdown", "content": content}


def _graph_entity(site: _Site, entity_id: str, name: str | None, color: str) -> Card:
    """Return one `statistics-graph` row; `color` only where HA reads it (2026.6+)."""
    row: Card = {"entity": entity_id}
    if name is not None:
        row["name"] = name
    if site.has["entity_color"]:
        row["color"] = color
    return row


def _graph(
    entities: list[str] | list[Card],
    stat_types: list[str],
    chart_type: str = "line",
    *,
    title: str | None = None,
    period: str | None = None,
) -> Card | None:
    """Return a statistics graph that follows the history view's picker (D12 §9 1)."""
    if not entities:
        return None
    card: Card = {
        "type": "statistics-graph",
        "entities": entities,
        "stat_types": stat_types,
        "chart_type": chart_type,
        "energy_date_selection": True,
        "collection_key": COLLECTION_KEY,
    }
    if title is not None:
        card["title"] = title
    if period is not None:
        card["period"] = period
    return card


def _timeline(
    site: _Site,
    loads: Sequence[LoadLayout],
    *,
    hours: int,
    options: list[int],
    deadline: str | None = None,
) -> Card | None:
    """Return the timeline (D12 §5.2): the plan's slots against the price forecast."""
    e = site.site.entities
    if "plan" not in e or "price_forecast" not in e:
        return None
    entities = {"plan": e["plan"], "price_forecast": e["price_forecast"]}
    if deadline is not None:
        entities["deadline"] = deadline
        show = ["plan", "price"]
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
        key.removeprefix("card_"): value for key, value in texts.items() if key.startswith("card_")
    }
