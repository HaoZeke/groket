#!/usr/bin/env python3
"""Build the groket pack from the geometric three-bar mark.

The approved still is ``source/approved.jpg``. The mark itself is drawn in
SVG (needles, fins, three turn bars, three status caps). The wordmark is outlined Fira Sans ExtraBold (SIL OFL, Mozilla / Telefónica).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source" / "approved.jpg"
SVG = ROOT / "svg"
PNG = ROOT / "png"
FONT = ROOT / "fonts" / "FiraSans-ExtraBold.ttf"

INK = "#282828"
CREAM = "#FBF1C7"
GREEN = "#98971A"
RED = "#CC241D"
YELLOW = "#D79921"
INK_RGB = (40, 40, 40)
CREAM_RGB = (251, 241, 199)

MARK_VB = (900, 380)
# Drawn silhouette (needles + fins), not the empty viewBox.
MARK_BOX = (12.0, 64.0, 872.0, 322.0)
TILE = 1024
TILE_PAD = 88
TILE_RADIUS = 220


def mark_group(ink: str, green: str, red: str, yellow: str) -> str:
    """Needles, fins, three bars, three caps. ViewBox ``0 0 900 380``."""
    # Tail/nose join the stack as a thin needle (half-height 14), not a
    # shaft through the middle bar. The nose sits on the middle bar, so
    # that cap is complete — the rocket flies toward success. Top is
    # failed; the short bar is running.
    return f"""\
  <g id="groket-mark" fill="{ink}">
    <polygon points="12,193 318,179 318,207"/>
    <polygon points="570,179 570,207 872,193"/>
    <polygon points="268,64 392,64 428,117 304,117"/>
    <polygon points="268,322 392,322 428,269 304,269"/>
    <rect x="318" y="117" width="208" height="44"/>
    <rect x="318" y="171" width="208" height="44"/>
    <rect x="318" y="225" width="156" height="44"/>
  </g>
  <g id="groket-caps">
    <rect x="526" y="117" width="44" height="44" fill="{red}"/>
    <rect x="526" y="171" width="44" height="44" fill="{green}"/>
    <rect x="474" y="225" width="44" height="44" fill="{yellow}"/>
  </g>"""


def svg_doc(title: str, body: str, vb: tuple[int, int]) -> str:
    w, h = vb
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{title}">\n'
        f"  <title>{title}</title>\n"
        f"{body}\n"
        f"</svg>\n"
    )


def write_svg(name: str, title: str, body: str, vb: tuple[int, int]) -> Path:
    path = SVG / name
    path.write_text(svg_doc(title, body, vb))
    return path


def word_paths(text: str, size: float, origin: tuple[float, float]) -> tuple[str, float]:
    font = TTFont(FONT)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    x, y = origin
    tracking = -0.04 * size
    chunks: list[str] = []
    for i, ch in enumerate(text):
        glyph = glyph_set[cmap[ord(ch)]]
        pen = SVGPathPen(glyph_set)
        glyph.draw(TransformPen(pen, Transform(scale, 0, 0, -scale, x, y)))
        chunks.append(f'<path d="{pen.getCommands()}"/>')
        adv = glyph.width * scale
        if i + 1 < len(text):
            adv += tracking
        x += adv
    return "\n    ".join(chunks), x - origin[0]


def rsvg(src: Path, dest: Path, *, width: int | None = None, height: int | None = None) -> None:
    bin_ = shutil.which("rsvg-convert")
    if bin_ is None:
        raise SystemExit("rsvg-convert not found (librsvg). On macOS: brew install librsvg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [bin_]
    if width is not None:
        cmd.extend(["-w", str(width)])
    if height is not None:
        cmd.extend(["-h", str(height)])
    cmd.extend([str(src), "-o", str(dest)])
    subprocess.check_call(cmd)


def tile_transform(tile: int, pad: int) -> tuple[float, float, float]:
    """Scale the silhouette to ``tile - 2*pad`` wide and center it."""
    x0, y0, x1, y1 = MARK_BOX
    scale = (tile - 2 * pad) / (x1 - x0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return tile / 2 - cx * scale, tile / 2 - cy * scale, scale


def rounded_svg(fill: str, mark_fill: str, green: str, red: str, yellow: str) -> str:
    """1024 tile. Silhouette centered, width-limited, even padding."""
    tx, ty, scale = tile_transform(TILE, TILE_PAD)
    inner = mark_group(mark_fill, green, red, yellow)
    return (
        f'  <rect width="{TILE}" height="{TILE}" rx="{TILE_RADIUS}" fill="{fill}"/>\n'
        f'  <g transform="translate({tx:.2f} {ty:.2f}) scale({scale:.4f})">\n'
        f"{inner}\n"
        f"  </g>"
    )


# Small mark lives on a terminal grid: 7 cells wide, 3 cells tall.
# Long bars are 6 + cap; the running bar is 4 + cap. One cell = one █.
SMALL_COLS = 7
SMALL_ROWS = 3
SMALL_LONG = 6
SMALL_SHORT = 4


def small_group(ink: str, green: str, red: str, yellow: str, *, cell: float = 1.0) -> str:
    """Three bars + caps on a 7×3 cell grid (TUI / CLI / favicon).

    Same stack as the rocket: failed, complete (nose), running (short).
    """
    c = cell
    long_w = SMALL_LONG * c
    short_w = SMALL_SHORT * c
    return f"""\
  <g id="groket-small">
    <rect x="0" y="0" width="{long_w}" height="{c}" fill="{ink}"/>
    <rect x="{long_w}" y="0" width="{c}" height="{c}" fill="{red}"/>
    <rect x="0" y="{c}" width="{long_w}" height="{c}" fill="{ink}"/>
    <rect x="{long_w}" y="{c}" width="{c}" height="{c}" fill="{green}"/>
    <rect x="0" y="{2 * c}" width="{short_w}" height="{c}" fill="{ink}"/>
    <rect x="{short_w}" y="{2 * c}" width="{c}" height="{c}" fill="{yellow}"/>
  </g>"""


def caps_group(green: str, red: str, yellow: str, *, cell: float = 1.0) -> str:
    """Three cap squares with a one-cell gap (one terminal row)."""
    c = cell
    return f"""\
  <g id="groket-caps-row">
    <rect x="0" y="0" width="{c}" height="{c}" fill="{green}"/>
    <rect x="{2 * c}" y="0" width="{c}" height="{c}" fill="{red}"/>
    <rect x="{4 * c}" y="0" width="{c}" height="{c}" fill="{yellow}"/>
  </g>"""


def small_tile(ink: str, green: str, red: str, yellow: str, tile: int) -> str:
    """Square favicon: 7×3 grid letterboxed, cell size limited by width."""
    cell = tile / SMALL_COLS
    mark_h = SMALL_ROWS * cell
    ty = (tile - mark_h) / 2
    return (
        f'  <rect width="{tile}" height="{tile}" fill="none"/>\n'
        f'  <g transform="translate(0 {ty:.4f})">\n'
        f"{small_group(ink, green, red, yellow, cell=cell)}\n"
        f"  </g>"
    )


def write_small_text() -> None:
    """Canonical Unicode small mark (same 7×3 grid as the SVG)."""
    long = "█" * SMALL_LONG + "█"
    short = "█" * SMALL_SHORT + "█"
    (ROOT / "small.txt").write_text(f"{long}\n{long}\n{short}\n", encoding="utf-8")
    (ROOT / "caps.txt").write_text("█ █ █\n", encoding="utf-8")


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing approved still: {SOURCE}")
    if not FONT.is_file():
        raise SystemExit(f"missing wordmark font: {FONT}")
    PNG.mkdir(parents=True, exist_ok=True)
    SVG.mkdir(parents=True, exist_ok=True)

    colour = mark_group(INK, GREEN, RED, YELLOW)
    mono = mark_group(INK, INK, INK, INK)
    reverse = mark_group(CREAM, GREEN, RED, YELLOW)

    mark = write_svg("groket-mark.svg", "groket", colour, MARK_VB)
    write_svg("groket-mark-mono.svg", "groket", mono, MARK_VB)
    write_svg(
        "groket-mark-reverse.svg",
        "groket",
        f'  <rect width="900" height="380" fill="{INK}"/>\n{reverse}',
        MARK_VB,
    )
    write_svg(
        "groket-small.svg",
        "groket",
        small_group(INK, GREEN, RED, YELLOW),
        (SMALL_COLS, SMALL_ROWS),
    )
    write_svg(
        "groket-caps.svg",
        "groket",
        caps_group(GREEN, RED, YELLOW),
        (5, 1),
    )
    write_svg(
        "groket-favicon.svg",
        "groket",
        small_tile(INK, GREEN, RED, YELLOW, 32),
        (32, 32),
    )
    write_small_text()
    write_svg(
        "groket-app-icon.svg",
        "groket",
        rounded_svg(CREAM, INK, GREEN, RED, YELLOW),
        (1024, 1024),
    )
    write_svg(
        "groket-app-icon-dark.svg",
        "groket",
        rounded_svg(INK, CREAM, GREEN, RED, YELLOW),
        (1024, 1024),
    )

    wp, ww = word_paths("groket", 72, (16, 78))
    word = write_svg(
        "groket-wordmark.svg",
        "groket",
        f'  <g fill="{INK}">\n    {wp}\n  </g>',
        (int(ww) + 32, 100),
    )

    rsvg(mark, PNG / "groket-mark.png", width=1200)
    rsvg(SVG / "groket-mark-mono.svg", PNG / "groket-mark-mono.png", width=1200)
    rsvg(SVG / "groket-mark-reverse.svg", PNG / "groket-mark-reverse.png", width=1200)
    rsvg(mark, PNG / "groket-mark@2x.png", width=2400)
    rsvg(word, PNG / "groket-wordmark.png", width=1200)
    _write_lockups(
        Image.open(PNG / "groket-mark.png").convert("RGBA"),
        Image.open(PNG / "groket-wordmark.png").convert("RGBA"),
    )
    rsvg(SVG / "groket-app-icon.svg", PNG / "groket-app-icon-1024.png", width=1024)
    for n in (512, 256):
        _resize_square(PNG / "groket-app-icon-1024.png", PNG / f"groket-app-icon-{n}.png", n)
    rsvg(SVG / "groket-app-icon-dark.svg", PNG / "groket-app-icon-dark-1024.png", width=1024)
    rsvg(SVG / "groket-small.svg", PNG / "groket-small.png", width=224)
    rsvg(SVG / "groket-caps.svg", PNG / "groket-caps.png", width=160)
    rsvg(SVG / "groket-favicon.svg", PNG / "groket-favicon-64.png", width=64)
    for n in (32, 16):
        rsvg(SVG / "groket-favicon.svg", PNG / f"groket-favicon-{n}.png", width=n)
    # Taskbar / tray / notify: dual-contrast badge (cream plate + ink rim +
    # 7×3 three-bar small mark). Works on dark and light panels at 16–22px.
    # Full rocket app icons collapse to mud; bare favicon ink vanishes on dark.
    for n in (32, 48, 64, 128):
        _write_tray_tile(PNG / f"groket-tray-{n}.png", n)


def _write_lockups(mark: Image.Image, word: Image.Image) -> None:
    mark_h = _scale_to_height(mark, 280)
    word_h = _scale_to_height(word, 72)
    horizontal = _compose_row(mark_h, word_h, gap=36)
    horizontal.save(PNG / "groket-lockup-horizontal.png", "PNG", optimize=True)
    write_svg(
        "groket-lockup-horizontal.svg",
        "groket",
        _image_href("groket-lockup-horizontal.png", horizontal.size),
        horizontal.size,
    )
    mark_s = _scale_to_height(mark, 520)
    word_s = _scale_to_width(word, int(mark_s.width * 0.72))
    stacked = _compose_stack(mark_s, word_s, gap=28)
    stacked.save(PNG / "groket-lockup-stacked.png", "PNG", optimize=True)
    write_svg(
        "groket-lockup-stacked.svg",
        "groket",
        _image_href("groket-lockup-stacked.png", stacked.size),
        stacked.size,
    )


def _scale_to_height(img: Image.Image, height: int) -> Image.Image:
    width = max(1, round(img.width * (height / img.height)))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def _scale_to_width(img: Image.Image, width: int) -> Image.Image:
    height = max(1, round(img.height * (width / img.width)))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def _compose_row(mark: Image.Image, word: Image.Image, gap: int) -> Image.Image:
    h = max(mark.height, word.height)
    canvas = Image.new("RGBA", (mark.width + gap + word.width, h), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (0, (h - mark.height) // 2))
    canvas.alpha_composite(word, (mark.width + gap, (h - word.height) // 2))
    return canvas


def _compose_stack(mark: Image.Image, word: Image.Image, gap: int) -> Image.Image:
    w = max(mark.width, word.width)
    canvas = Image.new("RGBA", (w, mark.height + gap + word.height), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((w - mark.width) // 2, 0))
    canvas.alpha_composite(word, ((w - word.width) // 2, mark.height + gap))
    return canvas


def _image_href(png_name: str, size: tuple[int, int]) -> str:
    w, h = size
    return (
        f'  <image href="../png/{png_name}" width="{w}" height="{h}" '
        f'preserveAspectRatio="xMidYMid meet"/>'
    )


def _resize_square(src: Path, dest: Path, size: int) -> None:
    im = Image.open(src).convert("RGBA")
    im.resize((size, size), Image.Resampling.LANCZOS).save(dest, "PNG", optimize=True)


def _hex_rgba(hex_s: str) -> tuple[int, int, int, int]:
    h = hex_s.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def _write_tray_tile(dest: Path, tile: int) -> None:
    """Dual-contrast taskbar badge: cream plate, ink rim, 7×3 three-bar mark.

    Dark desktops see the cream field. Light desktops see the ink rim + bars.
    Bars fill most of the face (small pad) so 16–22px panel sizes stay legible.
    """
    from PIL import ImageDraw

    cream = _hex_rgba(CREAM)
    ink = _hex_rgba(INK)
    red = _hex_rgba(RED)
    green = _hex_rgba(GREEN)
    yellow = _hex_rgba(YELLOW)

    # Transparent corners; rounded cream face.
    im = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    # Corner radius ~18% of edge (app-icon family).
    radius = max(2, round(tile * 0.18))
    # Border thick enough to survive downscale to ~16px.
    border = max(1, round(tile / 32))
    # Outer ink stroke, then cream inset (rim on light panels).
    draw.rounded_rectangle([0, 0, tile - 1, tile - 1], radius=radius, fill=ink)
    inset = border
    draw.rounded_rectangle(
        [inset, inset, tile - 1 - inset, tile - 1 - inset],
        radius=max(1, radius - inset),
        fill=cream,
    )

    # Three-bar grid: ~10% pad inside the cream face so cells stay chunky.
    face = tile - 2 * (inset + max(2, tile // 10))
    cell = max(2, face // SMALL_COLS)
    # Prefer height fit for 3 rows if needed.
    cell = min(cell, max(2, face // SMALL_ROWS))
    mark_w = SMALL_COLS * cell
    mark_h = SMALL_ROWS * cell
    ox = (tile - mark_w) // 2
    oy = (tile - mark_h) // 2
    rows = [(SMALL_LONG, red), (SMALL_LONG, green), (SMALL_SHORT, yellow)]
    for row, (bar_w, cap) in enumerate(rows):
        y0 = oy + row * cell
        y1 = y0 + cell - 1
        # Tiny gap between rows (1px) so bars separate when downscaled.
        if row > 0 and cell > 2:
            y0 += 1
        draw.rectangle([ox, y0, ox + bar_w * cell - 1, y1], fill=ink)
        draw.rectangle(
            [ox + bar_w * cell, y0, ox + (bar_w + 1) * cell - 1, y1],
            fill=cap,
        )
    im.save(dest, "PNG", optimize=True)


if __name__ == "__main__":
    main()
