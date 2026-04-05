"""The electrical connection, and every conversion that depends on it (D3 §5.1).

Per-phase quantities are amps, never watts: per-phase power is ill-defined on an
IT system with no neutral, and every per-phase limit - the main fuse, a circuit,
a charger - is an ampere rating anyway (D3 §2). Watts enter only through
`w_per_amp`, and only with the phase count of the thing being converted.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

__all__ = ["ElectricalProfile", "VoltageSystem"]

SQRT3 = math.sqrt(3.0)


class VoltageSystem(StrEnum):
    """The supply system, which fixes both voltages and the W/A table (D3 §4)."""

    IT_230 = "it_230"
    TN_400 = "tn_400"
    TT_400 = "tt_400"
    SPLIT_240 = "split_240"
    SINGLE_230 = "single_230"
    SINGLE_120 = "single_120"


# D3 §5.1. Only the electrically meaningful combinations are listed: two legs of
# a 400 V system and three phases of a single-phase supply are not conversions,
# they are configuration errors, and the flow catches them at the boundary.
_W_PER_AMP: dict[tuple[VoltageSystem, int], float] = {
    (VoltageSystem.IT_230, 1): 230.0,
    (VoltageSystem.IT_230, 3): SQRT3 * 230.0,
    (VoltageSystem.TN_400, 1): 230.0,
    (VoltageSystem.TN_400, 3): SQRT3 * 400.0,
    (VoltageSystem.TT_400, 1): 230.0,
    (VoltageSystem.TT_400, 3): SQRT3 * 400.0,
    (VoltageSystem.SPLIT_240, 1): 120.0,
    (VoltageSystem.SPLIT_240, 2): 240.0,
    (VoltageSystem.SINGLE_230, 1): 230.0,
    (VoltageSystem.SINGLE_120, 1): 120.0,
}

_V_LL: dict[VoltageSystem, float] = {
    VoltageSystem.IT_230: 230.0,
    VoltageSystem.TN_400: 400.0,
    VoltageSystem.TT_400: 400.0,
    VoltageSystem.SPLIT_240: 240.0,
    VoltageSystem.SINGLE_230: 230.0,
    VoltageSystem.SINGLE_120: 120.0,
}

_V_LN: dict[VoltageSystem, float | None] = {
    VoltageSystem.IT_230: None,  # no neutral: loads sit line-to-line
    VoltageSystem.TN_400: 230.0,
    VoltageSystem.TT_400: 230.0,
    VoltageSystem.SPLIT_240: 120.0,
    VoltageSystem.SINGLE_230: 230.0,
    VoltageSystem.SINGLE_120: 120.0,
}

# A split-phase service is one phase with two legs, and its main fuse rates both
# of them: a 200 A US panel delivers 48 kW, not 24 kW.
_SERVICE_PHASES: dict[VoltageSystem, int] = {VoltageSystem.SPLIT_240: 2}

# The plausibility band around the fuse (D3 §5.1). Symmetric because power is
# signed: export is a reading, not a fault (INV-19).
PLAUSIBLE_FACTOR = 1.2


@dataclass(frozen=True, slots=True)
class ElectricalProfile:
    """The site's electrical connection (D3 §4)."""

    system: VoltageSystem
    phases: Literal[1, 3]
    main_fuse_a: float
    per_phase_limit_a: float | None = None
    frequency_hz: Literal[50, 60] = 50

    def v_ll(self) -> float:
        """Line-to-line voltage."""
        return _V_LL[self.system]

    def v_ln(self) -> float | None:
        """Line-to-neutral voltage, or `None` on a system without a neutral."""
        return _V_LN[self.system]

    def w_per_amp(self, load_phases: Literal[1, 2, 3]) -> float:
        """Watts per ampere for something connected on `load_phases` (D3 §5.1)."""
        try:
            return _W_PER_AMP[(self.system, load_phases)]
        except KeyError:
            raise ValueError(
                f"{self.system} has no {load_phases}-phase connection: "
                f"{load_phases} phases on a {self.system} supply is a configuration error"
            ) from None

    def service_phases(self) -> Literal[1, 2, 3]:
        """Return the phase count the service itself delivers (D3 §5.1)."""
        legs = _SERVICE_PHASES.get(self.system)
        return legs if legs is not None else self.phases  # type: ignore[return-value]

    def fuse_w(self) -> float:
        """Return what the connection can deliver, in watts."""
        return self.main_fuse_a * self.w_per_amp(self.service_phases())

    def plausible_w(self) -> tuple[float, float]:
        """Return the band a grid reading must fall in to be believed (D3 §5.1)."""
        limit = PLAUSIBLE_FACTOR * self.fuse_w()
        return (-limit, limit)

    def phase_limit_a(self) -> float:
        """Return the per-phase limit in amps; the main fuse when none was configured."""
        return self.per_phase_limit_a if self.per_phase_limit_a is not None else self.main_fuse_a
