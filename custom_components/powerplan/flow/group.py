"""The group subentry flow (D8 §5.3, D6 §6).

    user (name, members, max_concurrent_w; from_stage, ceiling_fraction,
          starve_seconds in Advanced)
        → review ("Six floor loops share 2 kW when the hour gets tight; the
                   coldest gets it first.") → subentry
    reconfigure → the same form, pre-filled → review → update

One questionnaire step and a review line, as D8 §5.3 draws it; the review comes
before anything is saved (INV-67). The members are the site's load subentries,
picked by title, and the subentry stores their ids - the runtime builds D6's
`GroupCap` from exactly these fields (`runtime.build_groups`), so the rotation
rations what the household saw and nothing derived later (INV-66).
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
    GROUP_CEILING_FRACTION,
    GROUP_FROM_STAGE,
    GROUP_MAX_CONCURRENT_W,
    GROUP_MEMBERS,
    GROUP_STARVE_SECONDS,
    LOAD_PARAMS,
    SECTION_ADVANCED,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.allocation import default_max_concurrent_w
from custom_components.powerplan.flow.questionnaire import (
    advanced_section,
    as_duration,
    duration_selector,
    kw_selector,
    percent_selector,
    seconds_of,
)
from custom_components.powerplan.flow.text import Text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["GroupSubentryFlow", "cap_schema", "group_cap_w", "group_data", "group_schema"]

#: A group's own defaults (`GroupCap`'s constructor, D6 §5.6) - duplicated here
#: rather than imported so the flow layer stays decoupled from `core.allocation`'s
#: constraint classes, matching `flow/circuit.py`'s own convention.
DEFAULT_FROM_STAGE = 1
DEFAULT_CEILING_FRACTION = 0.85
DEFAULT_STARVE_SECONDS = 1800.0
#: Shown only until a load is picked and `default_max_concurrent_w` has something
#: to sum - a site with no loads yet never reaches this form (`no_loads`).
DEFAULT_MAX_CONCURRENT_W = 2000.0

#: What a group's shared cap can plausibly be, in watts.
MAX_CONCURRENT_W_MIN = 100.0
MAX_CONCURRENT_W_MAX = 50_000.0
STARVE_SECONDS_MIN = 60.0
STARVE_SECONDS_MAX = 7_200.0


#: The review's field for the shared cap: kW, stored as `max_concurrent_w` (CTL-15).
GROUP_MAX_CONCURRENT_KW = "max_concurrent_kw"


def group_cap_w(nameplates: Mapping[str, float], members: Sequence[str]) -> float:
    """Return D6 §6's default cap over the chosen members: the two largest nameplates summed."""
    chosen = [nameplates.get(member, 0.0) for member in members]
    return default_max_concurrent_w(chosen) or DEFAULT_MAX_CONCURRENT_W


def cap_schema(cap_w: float) -> vol.Schema:
    """Return the review's one field: the shared cap in kW, from the members chosen (LOAD-7)."""
    return vol.Schema(
        {
            vol.Required(GROUP_MAX_CONCURRENT_KW, default=round(cap_w / 1000.0, 3)): kw_selector(
                MAX_CONCURRENT_W_MIN, MAX_CONCURRENT_W_MAX
            )
        }
    )


def group_schema(
    members: Mapping[str, str],
    nameplates: Mapping[str, float],
    *,
    values: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Return the members step (D6 §6), pre-filled from `values`.

    The shared cap is not here: it is D6 §6's derivation over the members, and a
    form cannot react to a pick in the same form, so the review shows it
    computed from the members chosen, editable, in kW (D6 §6, review LOAD-7).
    """
    del nameplates
    given = values or {}
    starve = as_duration(float(given.get(GROUP_STARVE_SECONDS, DEFAULT_STARVE_SECONDS)))
    return vol.Schema(
        {
            vol.Required("name", default=given.get("name", vol.UNDEFINED)): TextSelector(),
            vol.Required(
                GROUP_MEMBERS, default=list(given.get(GROUP_MEMBERS) or [])
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=load_id, label=title)
                        for load_id, title in members.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    sort=True,
                )
            ),
            vol.Optional(SECTION_ADVANCED, default={}): advanced_section(
                {
                    vol.Optional(
                        GROUP_FROM_STAGE,
                        default=int(given.get(GROUP_FROM_STAGE, DEFAULT_FROM_STAGE)),
                    ): NumberSelector(
                        NumberSelectorConfig(min=0, max=4, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    # A share of the ceiling as a percent (CTL-2); stored as the fraction.
                    vol.Optional(
                        GROUP_CEILING_FRACTION,
                        default=round(
                            float(given.get(GROUP_CEILING_FRACTION, DEFAULT_CEILING_FRACTION))
                            * 100.0,
                            6,
                        ),
                    ): percent_selector(),
                    # Hours and minutes, stored in seconds (CTL-8).
                    vol.Optional(GROUP_STARVE_SECONDS, default=starve): duration_selector(),
                }
            ),
        }
    )


def group_data(user_input: Mapping[str, Any], cap_w: float) -> dict[str, Any]:
    """Return the subentry's data from the form (D6 §6 `GroupSubentryData`), W and seconds."""
    advanced = user_input.get(SECTION_ADVANCED) or {}
    ceiling = advanced.get(GROUP_CEILING_FRACTION)
    starve = seconds_of(advanced.get(GROUP_STARVE_SECONDS))
    return {
        GROUP_MEMBERS: [str(member) for member in user_input[GROUP_MEMBERS]],
        GROUP_MAX_CONCURRENT_W: float(cap_w),
        GROUP_FROM_STAGE: int(advanced.get(GROUP_FROM_STAGE, DEFAULT_FROM_STAGE)),
        GROUP_CEILING_FRACTION: (
            DEFAULT_CEILING_FRACTION if ceiling is None else round(float(ceiling) / 100.0, 6)
        ),
        GROUP_STARVE_SECONDS: DEFAULT_STARVE_SECONDS if starve is None else starve,
    }


class GroupSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one group (D8 §5.3)."""

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

    def _nameplates(self) -> dict[str, float]:
        """Return each load's nameplate watts, for `max_concurrent_w`'s suggested default."""
        return {
            subentry.subentry_id: float(
                (subentry.data.get(LOAD_PARAMS) or {}).get("nameplate_w") or 0.0
            )
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_LOAD
        }

    async def _ask(
        self, step_id: str, user_input: dict[str, Any] | None, stored: Mapping[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the questionnaire, or take its answers on to the review."""
        members = self._members()
        if not members:
            return self.async_abort(reason="no_loads")
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [str(member) for member in user_input.get(GROUP_MEMBERS) or ()]
            name = str(user_input.get("name") or "").strip()
            if not name:
                errors["name"] = "no_name"
            elif not chosen:
                errors[GROUP_MEMBERS] = "no_members"
            elif any(member not in members for member in chosen):
                errors[GROUP_MEMBERS] = "unknown_member"
            else:
                self._name = name
                stored_cap = (stored or {}).get(GROUP_MAX_CONCURRENT_W)
                same = stored is not None and sorted(chosen) == sorted(stored[GROUP_MEMBERS])
                cap = (
                    float(stored_cap)
                    if same and stored_cap is not None
                    else group_cap_w(self._nameplates(), chosen)
                )
                self._answers = group_data(user_input, cap)
                return await self.async_step_review()
        values: Mapping[str, Any] | None = user_input
        if values is None and stored is not None:
            values = {**stored, "name": self._name}
        return self.async_show_form(
            step_id=step_id,
            data_schema=group_schema(members, self._nameplates(), values=values),
            errors=errors or None,
        )

    async def _placeholders(self) -> dict[str, str]:
        """Return the review line's data: D6 §6's sentence with this group's numbers.

        The members joined with the language's own "og"/"and" (review NEW-4);
        numbers for the language.
        """
        assert self._answers is not None
        text = await Text.load(self.hass)
        members = self._members()
        answers = self._answers
        titles = [members[member] for member in answers[GROUP_MEMBERS] if member in members]
        return {
            "name": str(self._name),
            "members": text.join(titles),
            "max_concurrent_kw": text.number(answers[GROUP_MAX_CONCURRENT_W] / 1000.0),
            "from_stage": str(answers[GROUP_FROM_STAGE]),
            "ceiling_fraction": text.number(answers[GROUP_CEILING_FRACTION] * 100.0),
            "starve_min": text.number(answers[GROUP_STARVE_SECONDS] / 60.0),
        }

    # ------------------------------------------------------------------- steps

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, members and the shared cap, in one step."""
        return await self._ask("user", user_input, None)

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the review line (INV-67); confirming saves the group."""
        assert self._answers is not None
        if user_input is not None:
            kw = user_input.get(GROUP_MAX_CONCURRENT_KW)
            if kw is not None:
                self._answers[GROUP_MAX_CONCURRENT_W] = round(float(kw) * 1000.0, 3)
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
            data_schema=cap_schema(self._answers[GROUP_MAX_CONCURRENT_W]),
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
