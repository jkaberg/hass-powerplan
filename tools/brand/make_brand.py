"""Generate PowerPlan brand assets (icon + logo) as SVG and PNG.

Outputs follow the Home Assistant local brand spec (HA 2026.3+):
custom_components/powerplan/brand/{icon,icon@2x,logo,logo@2x,dark_logo,dark_logo@2x}.png

Writes to `tools/brand/out/` and reads Inter from `tools/brand/fonts/package/files/`
(the npm package `@fontsource/inter`, unpacked); both are git-ignored. Needs
`cairosvg` and `fonttools`, which are not project dependencies - see README.md.
"""

import math
from pathlib import Path

import cairosvg
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).parent
FONTS = HERE / "fonts" / "package" / "files"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- palette
TEAL = "#12B3A8"
BLUE = "#0A5FA8"
AMBER = "#FFC53D"
AMBER_DARK = "#F29D0B"
INK = "#0D2433"
TEAL_TEXT = "#0B8F87"
TEAL_TEXT_DARK = "#5EE0D2"

# ---------------------------------------------------------------- shapes
BOLT = "M140 30 L84 132 L122 132 L108 214 L178 104 L138 104 L160 30 Z"


def tile(defs_id: str) -> str:
    """Return the rounded gradient tile and the bolt's gradient."""
    return f"""
  <defs>
    <linearGradient id="{defs_id}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{TEAL}"/>
      <stop offset="1" stop-color="{BLUE}"/>
    </linearGradient>
    <linearGradient id="{defs_id}b" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{AMBER}"/>
      <stop offset="1" stop-color="{AMBER_DARK}"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="58" fill="url(#{defs_id})"/>"""


def concept_a(gid: str = "g") -> str:
    """Hour bars kept under a capacity line, with a power bolt."""
    # x, top  (baseline 208, width 22). Every bar AND the bolt stay under the
    # dashed capacity line at y=56 - the tallest hour fills up to just below it.
    bars = [
        f'<rect x="{x}" y="{top}" width="22" height="{208 - top}" rx="7" '
        f'fill="#FFFFFF" fill-opacity="0.92"/>'
        for x, top in [(34, 150), (64, 112), (170, 126), (200, 82)]
    ]
    bolt = "M134.7 76 L93.2 151.5 L121.3 151.5 L111 212.2 L162.8 130.8 L133.2 130.8 L149.5 76 Z"
    return f"""{tile(gid)}
  <line x1="37" y1="56" x2="219" y2="56" stroke="#FFFFFF" stroke-width="8"
        stroke-linecap="round" stroke-dasharray="14 14" stroke-opacity="0.95"/>
  {"".join(bars)}
  <path d="{bolt}" fill="url(#{gid}b)" stroke="url(#{gid}b)" stroke-width="10"
        stroke-linejoin="round"/>"""


def concept_b(gid: str = "g") -> str:
    """P monogram with a bolt through the bowl."""
    return f"""{tile(gid)}
  <path d="M78 212 V52 H138 A52 52 0 0 1 138 156 H112" fill="none" stroke="#FFFFFF"
        stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M150 70 L118 126 L140 126 L130 176 L172 110 L150 110 L162 70 Z"
        fill="url(#{gid}b)" stroke="url(#{gid}b)" stroke-width="7" stroke-linejoin="round"/>"""


def concept_c(gid: str = "g") -> str:
    """24-hour dial with planned (cheap) hours highlighted and a bolt."""
    segs = []
    cx = cy = 128
    r = 82
    planned = {0, 1, 2, 3, 4, 12, 13, 22, 23}
    for h in range(24):
        a0 = math.radians(h * 15 - 90 + 2.2)
        a1 = math.radians((h + 1) * 15 - 90 - 2.2)
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        op = "1" if h in planned else "0.35"
        segs.append(
            f'<path d="M{x0:.1f} {y0:.1f} A{r} {r} 0 0 1 {x1:.1f} {y1:.1f}" '
            f'stroke="#FFFFFF" stroke-opacity="{op}" stroke-width="22" fill="none"/>'
        )
    return f"""{tile(gid)}
  {"".join(segs)}
  <path d="M136 72 L100 136 L126 136 L116 188 L160 118 L134 118 L148 72 Z"
        fill="url(#{gid}b)" stroke="url(#{gid}b)" stroke-width="7" stroke-linejoin="round"/>"""


def svg_icon(body: str) -> str:
    """Wrap an icon body in a 256 × 256 SVG document."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        f'width="256" height="256">{body}\n</svg>\n'
    )


# ---------------------------------------------------------------- wordmark
def text_path(
    text: str, font_file: str, size: float, x: float, baseline: float, *, tracking: float = 0.0
) -> tuple[str, float]:
    """Return (svg path d, advance width) for text outlined from a font."""
    font = TTFont(FONTS / font_file)
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    upm = font["head"].unitsPerEm
    scale = size / upm
    hmtx = font["hmtx"]
    kern = {}
    pen = SVGPathPen(gs)
    cursor = 0.0
    prev = None
    for ch in text:
        gname = cmap[ord(ch)]
        if prev is not None:
            cursor += kern.get((prev, gname), 0)
        tp = TransformPen(pen, (scale, 0, 0, -scale, x + cursor * scale, baseline))
        gs[gname].draw(tp)
        cursor += hmtx[gname][0] + tracking * upm
        prev = gname
    return pen.getCommands(), cursor * scale


def svg_logo(icon_body: str, dark: bool) -> str:
    """Return the wordmark: the icon, then "Power" (700) and "Plan" (400), outlined."""
    h = 256
    size = 132
    baseline = 128 + size * 0.36
    x0 = 256 + 44
    power_d, w1 = text_path(
        "Power", "inter-latin-700-normal.woff", size, x0, baseline, tracking=-0.02
    )
    plan_d, w2 = text_path(
        "Plan", "inter-latin-400-normal.woff", size, x0 + w1 + 4, baseline, tracking=-0.02
    )
    width = int(x0 + w1 + 4 + w2 + 8)
    c1 = "#FFFFFF" if dark else INK
    c2 = TEAL_TEXT_DARK if dark else TEAL_TEXT
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {h}" '
        f'width="{width}" height="{h}">{icon_body}\n'
        f'  <path d="{power_d}" fill="{c1}"/>\n'
        f'  <path d="{plan_d}" fill="{c2}"/>\n</svg>\n'
    )


def render(svg: str, name: str, height: int) -> None:
    """Write `name`.svg and `name`.png, the PNG `height` px high, to `OUT`."""
    (OUT / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(
        bytestring=svg.encode(), write_to=str(OUT / f"{name}.png"), output_height=height
    )


if __name__ == "__main__":
    concepts = {"a": concept_a, "b": concept_b, "c": concept_c}
    for key, fn in concepts.items():
        svg = svg_icon(fn())
        (OUT / f"concept_{key}.svg").write_text(svg)
        for px in (512, 64, 32):
            cairosvg.svg2png(
                bytestring=svg.encode(),
                write_to=str(OUT / f"concept_{key}_{px}.png"),
                output_width=px,
                output_height=px,
            )

    # Final brand set from the recommended concept (A)
    icon = svg_icon(concept_a())
    render(icon, "icon", 256)
    render(icon, "icon@2x", 512)
    for dark in (False, True):
        prefix = "dark_" if dark else ""
        logo = svg_logo(concept_a(), dark)
        render(logo, f"{prefix}logo", 128)
        render(logo, f"{prefix}logo@2x", 256)
    print("done")
