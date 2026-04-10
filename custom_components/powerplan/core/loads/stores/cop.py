"""COP and efficiency curves (D4 §3, §5.14, §6.4).

A heat pump is described by a rated power and a **curve**, both asked for and
both editable, and powerplan computes forward from them: expected draw is heat
demand ÷ COP(T_out), capped at rated. The curve is what makes the priority order
come out right - at COP 3 the pump delivers three kilowatt-hours of heat per
kilowatt-hour drawn where a resistive loop delivers one, so shedding the pump to
protect the loop trades 3 kW of heat for 1.78 kW and is strictly backwards.

The three defaults are D4 §6.4's: the A2A curve measured in the reference house,
and typical SCOP data for air-to-water and ground-source. A resistive load is
`CopCurve.flat(1.0)`, and a boiler is `flat(η)`.

The `heat_pump` type is WP3.4; the curve lives here because the module list in
D4 §3 puts it here and because a zone (D6) compares carriers with it.
"""

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["DEFAULT_COP_CURVES", "CopCurve"]


@dataclass(frozen=True, slots=True)
class CopCurve:
    """COP against outdoor temperature, linear between its anchors (D4 §5.14)."""

    points: tuple[tuple[float, float], ...]

    @classmethod
    def flat(cls, value: float) -> CopCurve:
        """Return a constant efficiency: 1.0 resistive, η for a boiler, 3.5–4.5 GSHP."""
        return cls(points=((0.0, value),))

    @classmethod
    def from_mapping(cls, mapping: Mapping[float, float]) -> CopCurve:
        """Build a curve from `{outdoor_c: cop}`, sorted on the way in."""
        return cls(points=tuple(sorted((float(k), float(v)) for k, v in mapping.items())))

    def at(self, outdoor_c: float) -> float:
        """COP at `outdoor_c`, held flat outside the anchors.

        Flat rather than extrapolated: a curve fitted between −15 and +15 °C says
        nothing about −30, and a linear extrapolation there would invent a COP
        below 1 - a heat pump that is worse than a resistor.
        """
        points = self.points
        if len(points) == 1:
            return points[0][1]
        if outdoor_c <= points[0][0]:
            return points[0][1]
        if outdoor_c >= points[-1][0]:
            return points[-1][1]
        for (low_t, low_cop), (high_t, high_cop) in itertools.pairwise(points):
            if low_t <= outdoor_c <= high_t:
                span = high_t - low_t
                if span == 0.0:
                    return high_cop
                return low_cop + (high_cop - low_cop) * (outdoor_c - low_t) / span
        return points[-1][1]

    def draw_w(self, heat_w: float, outdoor_c: float, rated_w: float) -> float:
        """Electrical draw for `heat_w` of heat, capped at the rated power (§5.14)."""
        cop = self.at(outdoor_c)
        return min(rated_w, heat_w / cop) if cop > 0.0 else rated_w


#: D4 §6.4: A2A from the reference house's own measurements, A2W and GSHP from
#: typical SCOP data. Shown to the user and editable - never a hidden constant.
DEFAULT_COP_CURVES: Final[Mapping[str, CopCurve]] = {
    "a2a": CopCurve.from_mapping({-15: 1.8, -10: 2.2, -5: 2.6, 0: 3.0, 7: 3.8, 15: 4.5}),
    "a2w": CopCurve.from_mapping({-15: 1.6, -7: 2.2, 2: 2.9, 7: 3.4, 15: 4.0}),
    "gshp": CopCurve.from_mapping({-15: 3.5, 0: 3.8, 15: 4.5}),
}
