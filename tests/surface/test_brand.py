"""D8 §9 20, the brand half: the six PNGs at their sizes, the manifest's name and docs.

The sizes are the ones `tools/brand/README.md` documents. D8 §9 20's device-info half -
manufacturer "PowerPlan", a translated model, `sw_version` = the manifest version - is
WP U.4's (review BR-3) and is not tested here.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: (width, height) per file, from `tools/brand/README.md`.
SIZES = {
    "icon.png": (256, 256),
    "icon@2x.png": (512, 512),
    "logo.png": (484, 128),
    "logo@2x.png": (967, 256),
    "dark_logo.png": (484, 128),
    "dark_logo@2x.png": (967, 256),
}


def _png_size(path: Path) -> tuple[int, int]:
    """Read (width, height) from the IHDR chunk, which the PNG spec puts first."""
    head = path.read_bytes()[:24]
    assert head[:8] == PNG_SIGNATURE, f"{path.name} is not a PNG"
    length, kind = struct.unpack(">I4s", head[8:16])
    assert (length, kind) == (13, b"IHDR"), f"{path.name} does not start with IHDR"
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def _manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
    return loaded


@pytest.mark.parametrize(("name", "size"), SIZES.items())
def test_20_brand_png_at_its_documented_size(name: str, size: tuple[int, int]) -> None:
    """Each of the six files HA 2026.3+ serves from `brand/` is a PNG of its documented size."""
    assert _png_size(INTEGRATION / "brand" / name) == size


def test_20_manifest_name_and_documentation() -> None:
    """The name is "PowerPlan" on the domain `powerplan`; the help icon opens the public docs."""
    manifest = _manifest()
    assert manifest["domain"] == "powerplan"
    assert manifest["name"] == "PowerPlan"
    assert manifest["documentation"] == "https://github.com/jkaberg/hass-powerplan/tree/main/docs"
