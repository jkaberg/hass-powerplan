"""The zone subentry flow (D8 §5.3, D6 §6).

    user (name, members, never_substitute; min_cop, switch_hysteresis,
          min_dwell_min, switch_confirm_s, capacity_penalty in Advanced)
        → review ("The living-room slab and the first-floor heat pump heat the
                   same space; powerplan runs whichever is cheaper per kWh of
                   heat, and prefers the heat pump when the ceiling is at
                   risk.") → subentry
    reconfigure → the same form, pre-filled → review → update

One questionnaire step and a review line, matching `flow/group.py`'s shape.
A zone's members are also its sources - the "substitution pair" scope D6 §5.7
covers (HLD §9 phase 5's own title): a room two loads can both heat, not yet a
hydronic loop driven by a separate zero-nameplate member (v1.x, D4 §5.15).
Carrier and efficiency come from D4, never asked here (D6 §6): a heat pump's
own COP curve (`heat_pump.curve_of`) if the source is one, a flat 1.0 (resistive)
otherwise - every load type but `heat_pump` defaults to electricity with no
efficiency loss, and none offers a non-electric carrier yet (WP5.3's own gap,
`design/DECISIONS.md`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from custom_components.powerplan.const import (
    SECTION_ADVANCED,
    SUBENTRY_LOAD,
    ZONE_CAPACITY_PENALTY,
    ZONE_MEMBERS,
    ZONE_MIN_COP,
    ZONE_MIN_DWELL_MIN,
    ZONE_NEVER_SUBSTITUTE,
    ZONE_SWITCH_CONFIRM_S,
    ZONE_SWITCH_HYSTERESIS,
)
from custom_components.powerplan.core.allocation.constraints.zone import (
    DEFAULT_CAPACITY_PENALTY,
    DEFAULT_MIN_COP,
    DEFAULT_MIN_DWELL_MIN,
    DEFAULT_SWITCH_CONFIRM_S,
    DEFAULT_SWITCH_HYSTERESIS,
)
from custom_components.powerplan.flow.questionnaire import advanced_section
from custom_components.powerplan.flow.text import Text

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ZoneSubentryFlow", "zone_data", "zone_schema"]

#: A zone needs at least two sources - below that there is nothing to
#: substitute to, and `Zone.prepare()` stands aside regardless (INV-42).
_MIN_MEMBERS = 2

MIN_COP_MIN = 1.0
MIN_COP_MAX = 6.0
SWITCH_HYSTERESIS_MIN = 0.0
SWITCH_HYSTERESIS_MAX = 1.0
MIN_DWELL_MIN_MIN = 0.0
MIN_DWELL_MIN_MAX = 240.0
SWITCH_CONFIRM_S_MIN = 0.0
SWITCH_CONFIRM_S_MAX = 7_200.0
CAPACITY_PENALTY_MIN = 0.0
CAPACITY_PENALTY_MAX = 10.0


def zone_schema(
    members: Mapping[str, str], *, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Return the one questionnaire step (D6 §6), pre-filled from `values`."""
    given = values or {}
    return vol.Schema(
        {
            vol.Required("name", default=given.get("name", vol.UNDEFINED)): TextSelector(),
            vol.Required(ZONE_MEMBERS, default=list(given.get(ZONE_MEMBERS) or [])): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=load_id, label=title)
                        for load_id, title in members.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    sort=False,
                )
            ),
            vol.Optional(
                ZONE_NEVER_SUBSTITUTE, default=list(given.get(ZONE_NEVER_SUBSTITUTE) or [])
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=load_id, label=title)
                        for load_id, title in members.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    sort=False,
                )
            ),
            vol.Optional(SECTION_ADVANCED, default={}): advanced_section(
                {
                    vol.Optional(
                        ZONE_MIN_COP,
                        default=float(given.get(ZONE_MIN_COP, DEFAULT_MIN_COP)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_COP_MIN, max=MIN_COP_MAX, step=0.1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        ZONE_SWITCH_HYSTERESIS,
                        default=float(given.get(ZONE_SWITCH_HYSTERESIS, DEFAULT_SWITCH_HYSTERESIS)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=SWITCH_HYSTERESIS_MIN,
                            max=SWITCH_HYSTERESIS_MAX,
                            step=0.01,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        ZONE_MIN_DWELL_MIN,
                        default=float(given.get(ZONE_MIN_DWELL_MIN, DEFAULT_MIN_DWELL_MIN)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_DWELL_MIN_MIN,
                            max=MIN_DWELL_MIN_MAX,
                            step=5,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        ZONE_SWITCH_CONFIRM_S,
                        default=float(given.get(ZONE_SWITCH_CONFIRM_S, DEFAULT_SWITCH_CONFIRM_S)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=SWITCH_CONFIRM_S_MIN,
                            max=SWITCH_CONFIRM_S_MAX,
                            step=60,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        ZONE_CAPACITY_PENALTY,
                        default=float(
                            given.get(ZONE_CAPACITY_PENALTY, float(DEFAULT_CAPACITY_PENALTY))
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=CAPACITY_PENALTY_MIN,
                            max=CAPACITY_PENALTY_MAX,
                            step=0.05,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        }
    )


def zone_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the subentry's data from the form (D6 §6 `ZoneSubentryData`)."""
    advanced = user_input.get(SECTION_ADVANCED) or {}
    return {
        ZONE_MEMBERS: [str(member) for member in user_input[ZONE_MEMBERS]],
        ZONE_NEVER_SUBSTITUTE: [
            str(member) for member in user_input.get(ZONE_NEVER_SUBSTITUTE) or ()
        ],
        ZONE_MIN_COP: float(advanced.get(ZONE_MIN_COP, DEFAULT_MIN_COP)),
        ZONE_SWITCH_HYSTERESIS: float(
            advanced.get(ZONE_SWITCH_HYSTERESIS, DEFAULT_SWITCH_HYSTERESIS)
        ),
        ZONE_MIN_DWELL_MIN: float(advanced.get(ZONE_MIN_DWELL_MIN, DEFAULT_MIN_DWELL_MIN)),
        ZONE_SWITCH_CONFIRM_S: float(advanced.get(ZONE_SWITCH_CONFIRM_S, DEFAULT_SWITCH_CONFIRM_S)),
        ZONE_CAPACITY_PENALTY: float(
            advanced.get(ZONE_CAPACITY_PENALTY, float(DEFAULT_CAPACITY_PENALTY))
        ),
    }


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one zone (D8 §5.3)."""

    def __init__(self) -> None:
        """Start empty; the form fills `_answers`, the review saves it."""
        self._answers: dict[str, Any] | None = None
        self._name: str | None = None

    # ----------------------------------------------------------------- helpers

    def _members(self) -> dict[str, str]:
        """Return the site's loads by subentry id, titled, for the members pick."""
        return {
            subentry.subentry_id: subentry.title
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_LOAD
        }

    async def _ask(
        self, step_id: str, user_input: dict[str, Any] | None, stored: Mapping[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the questionnaire, or take its answers on to the review."""
        members = self._members()
        if len(members) < _MIN_MEMBERS:
            return self.async_abort(reason="not_enough_loads")
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [str(member) for member in user_input.get(ZONE_MEMBERS) or ()]
            never = [str(member) for member in user_input.get(ZONE_NEVER_SUBSTITUTE) or ()]
            name = str(user_input.get("name") or "").strip()
            if not name:
                errors["name"] = "no_name"
            elif len(chosen) < _MIN_MEMBERS:
                errors[ZONE_MEMBERS] = "not_enough_members"
            elif any(member not in members for member in chosen):
                errors[ZONE_MEMBERS] = "unknown_member"
            elif any(member not in chosen for member in never):
                errors[ZONE_NEVER_SUBSTITUTE] = "unknown_member"
            else:
                self._name = name
                self._answers = zone_data(user_input)
                return await self.async_step_review()
        values: Mapping[str, Any] | None = user_input
        if values is None and stored is not None:
            values = {**stored, "name": self._name}
        return self.async_show_form(
            step_id=step_id,
            data_schema=zone_schema(members, values=values),
            errors=errors or None,
        )

    async def _placeholders(self) -> dict[str, str]:
        """Return the review line's data: D6 §6's sentence with this room's numbers.

        The members joined with the language's own "og"/"and" (review NEW-4);
        numbers for the language.
        """
        assert self._answers is not None
        text = await Text.load(self.hass)
        members = self._members()
        answers = self._answers
        titles = [members[member] for member in answers[ZONE_MEMBERS] if member in members]
        never_titles = [
            members[member] for member in answers[ZONE_NEVER_SUBSTITUTE] if member in members
        ]
        return {
            "name": str(self._name),
            "members": text.join(titles),
            "never_substitute": text.join(never_titles)
            if never_titles
            else text.word("text", "none"),
            "min_cop": text.number(answers[ZONE_MIN_COP]),
        }

    # ------------------------------------------------------------------- steps

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, members and which of them are never substituted, in one step."""
        return await self._ask("user", user_input, None)

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the review line (INV-67); confirming saves the zone."""
        assert self._answers is not None
        if user_input is not None:
            if self.source == "reconfigure":
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=str(self._name),
                    data=self._answers,
                )
            return self.async_create_entry(title=str(self._name), data=self._answers)
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders=await self._placeholders(),
            last_step=True,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the same form, pre-filled from the subentry (D8 §5.3)."""
        subentry = self._get_reconfigure_subentry()
        if self._name is None:
            self._name = subentry.title
        return await self._ask("reconfigure", user_input, dict(subentry.data))
