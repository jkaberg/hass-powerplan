"""Session-wide test configuration (D9 §3).

`tests/core/` runs with no Home Assistant: nothing there requests `hass`, so
the autouse fixture below does nothing and no `HomeAssistant` instance is ever
created for a core test. `tests/providers/`, `tests/flows/` and `tests/e2e/`
request `hass` and therefore get `enable_custom_integrations`.
"""

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Load `custom_components/powerplan` for every test that starts HA."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
