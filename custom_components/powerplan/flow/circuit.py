"""The circuit subentry flow (D8 §5.3, D6 §6).

    user (name, fuse, phases, members, sub-meter; unmetered allowance in Advanced)
        → review ("The garage circuit is fused at 32 A: the charger and the
                   sauna will never exceed it together.") → subentry
    reconfigure → the same form, pre-filled → review → update

One questionnaire step and a review line, as D8 §5.3 draws it; the review comes
before anything is saved (INV-67). The members are the site's load subentries,
picked by title, and the subentry stores their ids - the runtime builds D6's
`CircuitSpec` from exactly these fields (`runtime.build_circuits`), so the
constraint binds what the household saw and nothing derived later (INV-66).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from custom_components.powerplan.const import (
    CIRCUIT_FUSE_A,
    CIRCUIT_MEMBERS,
    CIRCUIT_PHASES,
    CIRCUIT_SUB_METER,
    CIRCUIT_UNMETERED_W,
    CONF_ELECTRICAL,
    SECTION_ADVANCED,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.flow.questionnaire import (
    CIRCUIT_FUSE_SIZES,
    advanced_section,
    amps_of,
    fuse_selector,
    kw_selector,
)
from custom_components.powerplan.flow.text import Text, entity_name

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CircuitSubentryFlow", "circuit_data", "circuit_schema"]

#: The rating a fresh form shows: the most common sub-fuse in a house.
DEFAULT_FUSE_A = 16.0
#: What a sub-fuse can plausibly be, in amps.
FUSE_A_MIN = 1.0
FUSE_A_MAX = 200.0
#: The unmetered allowance's range, in watts (D6 §6 `unmetered_w` 0).
UNMETERED_W_MAX = 20_000.0
#: The form's field for it: kW, stored as `unmetered_w` (CTL-15).
CIRCUIT_UNMETERED_KW = "unmetered_kw"


def circuit_schema(
    members: Mapping[str, str], *, site_phases: int, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Return the one questionnaire step (D6 §6), pre-filled from `values`."""
    given = values or {}
    fuse = given.get(CIRCUIT_FUSE_A, DEFAULT_FUSE_A)
    amps = amps_of(fuse)
    return vol.Schema(
        {
            vol.Required("name", default=given.get("name", vol.UNDEFINED)): TextSelector(),
            # A pick of breaker sizes, "Annet…" typed in, no clear button (CTL-1, LOAD-10).
            vol.Required(
                CIRCUIT_FUSE_A, default=f"{amps:g}" if amps is not None else str(fuse)
            ): fuse_selector(CIRCUIT_FUSE_SIZES),
            # The same control as the site's phases (CTL-14).
            vol.Required(
                CIRCUIT_PHASES, default=str(given.get(CIRCUIT_PHASES, site_phases))
            ): SelectSelector(
                SelectSelectorConfig(
                    options=["1", "3"],
                    mode=SelectSelectorMode.LIST,
                    translation_key="phases",
                    sort=False,
                )
            ),
            vol.Required(
                CIRCUIT_MEMBERS, default=list(given.get(CIRCUIT_MEMBERS) or [])
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
            vol.Optional(
                CIRCUIT_SUB_METER,
                description={"suggested_value": given.get(CIRCUIT_SUB_METER)},
            ): EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power")),
            vol.Optional(SECTION_ADVANCED, default={}): advanced_section(
                {
                    # "Annet forbruk på kursen" in kW; stored in W (LOAD-8, CTL-15).
                    vol.Optional(
                        CIRCUIT_UNMETERED_KW,
                        default=float(given.get(CIRCUIT_UNMETERED_W, 0.0)) / 1000.0,
                    ): kw_selector(0.0, UNMETERED_W_MAX)
                }
            ),
        }
    )


def circuit_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the subentry's data from the form (D6 §6 `CircuitSubentryData`)."""
    advanced = user_input.get(SECTION_ADVANCED) or {}
    sub_meter = user_input.get(CIRCUIT_SUB_METER)
    return {
        CIRCUIT_FUSE_A: float(amps_of(user_input[CIRCUIT_FUSE_A]) or 0.0),
        CIRCUIT_PHASES: int(user_input[CIRCUIT_PHASES]),
        CIRCUIT_MEMBERS: [str(member) for member in user_input[CIRCUIT_MEMBERS]],
        CIRCUIT_SUB_METER: str(sub_meter) if sub_meter else None,
        CIRCUIT_UNMETERED_W: round(float(advanced.get(CIRCUIT_UNMETERED_KW, 0.0)) * 1000.0, 3),
    }


class CircuitSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one circuit (D8 §5.3)."""

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

    def _site_phases(self) -> int:
        electrical = dict(self._get_entry().data.get(CONF_ELECTRICAL) or {})
        return int(electrical.get("phases", 1))

    async def _ask(
        self, step_id: str, user_input: dict[str, Any] | None, stored: Mapping[str, Any] | None
    ) -> SubentryFlowResult:
        """Show the questionnaire, or take its answers on to the review."""
        members = self._members()
        if not members:
            return self.async_abort(reason="no_loads")
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [str(member) for member in user_input.get(CIRCUIT_MEMBERS) or ()]
            name = str(user_input.get("name") or "").strip()
            amps = amps_of(user_input.get(CIRCUIT_FUSE_A))
            if not name:
                errors["name"] = "no_name"
            elif amps is None or not FUSE_A_MIN <= amps <= FUSE_A_MAX:
                errors[CIRCUIT_FUSE_A] = "fuse_out_of_range"
            elif not chosen:
                errors[CIRCUIT_MEMBERS] = "no_members"
            elif any(member not in members for member in chosen):
                errors[CIRCUIT_MEMBERS] = "unknown_member"
            else:
                self._name = name
                self._answers = circuit_data(user_input)
                return await self.async_step_review()
        values: Mapping[str, Any] | None = user_input
        if values is None and stored is not None:
            values = {**stored, "name": self._name}
        return self.async_show_form(
            step_id=step_id,
            data_schema=circuit_schema(members, site_phases=self._site_phases(), values=values),
            errors=errors or None,
        )

    async def _placeholders(self) -> dict[str, str]:
        """Return the review line's data: D6 §6's sentence with this circuit's numbers.

        The members joined with the language's own "og"/"and" and the sub-meter
        by its name, never its id (review NEW-4, R2); numbers for the language.
        """
        assert self._answers is not None
        text = await Text.load(self.hass)
        members = self._members()
        answers = self._answers
        titles = [members[member] for member in answers[CIRCUIT_MEMBERS] if member in members]
        sub_meter = answers.get(CIRCUIT_SUB_METER)
        return {
            "name": str(self._name),
            "fuse_a": text.number(answers[CIRCUIT_FUSE_A]),
            "phases": str(answers[CIRCUIT_PHASES]),
            "members": text.join(titles),
            "sub_meter": entity_name(self.hass, sub_meter)
            if sub_meter
            else text.word("text", "none"),
            "unmetered_w": text.number(answers[CIRCUIT_UNMETERED_W]),
        }

    # ------------------------------------------------------------------- steps

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Name, fuse, phases, members and the optional sub-meter, in one step."""
        return await self._ask("user", user_input, None)

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the review line (INV-67); confirming saves the circuit."""
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
