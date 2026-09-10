"""Fixtures for the HA-level end-to-end day (D9 §5.10).

The captured meter, the Nord Pool entry and the two persons are the flow
tests' fixtures, re-exported: the site is created through the same flow the
flow tests drive, against the same entities. The store's read side is real I/O
(D-0091), so each test gets its own config directory.
"""

from tests.flows.conftest import (  # noqa: F401 - fixtures re-exported for this package
    ams_meter,
    nordpool_entry,
    norway_source,
    persons,
)
from tests.runtime.conftest import hass_config_dir  # noqa: F401
