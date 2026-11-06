"""Programmatic app icon for gemini-local-bridge.

Draws a modern, flat icon: a rounded-square tile with a teal->blue gradient,
a white "bridge" glyph (an arch over two pylons = connecting cloud to local),
and a small 4-point sparkle (a nod to Gemini). Rendered at high resolution and
downsampled for crisp anti-aliasing.

Public API:
    build_image(size=256) -> PIL.Image       # RGBA icon at any size
    save_png(path, size=256)                 # e.g. for the tray
    save_ico(path, sizes=(16,32,48,64,128,256))  # multi-size Windows .ico
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

# Brand palette (matches theme.COLORS accent).
_TOP = (20, 184, 138)  # teal-500
_BOTTOM = (14, 116, 144)  # cyan-700
_TILE_BG = (22, 27, 34)  # near-black fallback
_WHITE = (255, 255, 255, 255)
_SPARKLE = (255, 214, 102, 255)  # warm gold

_SS = 4  # supersample factor for smooth edges


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _draw_tile(size: int) -> Image.Image:
    """Rounded-square tile with a vertical teal->cyan gradient."""
    s = size * _SS
    radius = int(s * 0.24)

    # Gradient fill on a full canvas.
    grad = Image.new("RGB", (s, s), _TILE_BG)
    gdraw = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / max(1, s - 1)
        gdraw.line([(0, y), (s, y)], fill=_lerp(_TOP, _BOTTOM, t))

    # Mask to a rounded square.
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=radius, fill=255
    )

    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    return tile


def _draw_sparkle(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """4-point star (Gemini sparkle) centred at (cx, cy)."""
    # Concave 4-point star: outer tips at N/E/S/W, inner control points closer in.
    outer = r
    inner = r * 0.28
    pts = []
    for i in range(8):
        angle = math.pi / 2 * (i / 2) - math.pi / 2
        rad = outer if i % 2 == 0 else inner
        pts.append((cx + rad * math.cos(angle), cy + rad * math.sin(angle)))
    draw.polygon(pts, fill=_SPARKLE)


def build_image(size: int = 256) -> Image.Image:
    """Render the icon at *size* x *size* (RGBA)."""
    s = size * _SS
    tile = _draw_tile(size)
    draw = ImageDraw.Draw(tile)

    cx = s / 2
    # --- Bridge glyph (white) ---
    # Deck (horizontal bar).
    deck_y = int(s * 0.60)
    deck_h = int(s * 0.075)
    deck_left = int(s * 0.20)
    deck_right = int(s * 0.80)
    draw.rounded_rectangle(
        [deck_left, deck_y, deck_right, deck_y + deck_h],
        radius=deck_h // 2,
        fill=_WHITE,
    )

    # Arch above the deck.
    arch_top = int(s * 0.34)
    arch_box = [int(s * 0.30), arch_top, int(s * 0.70), deck_y + deck_h]
    draw.arc(arch_box, start=180, end=0, fill=_WHITE, width=int(s * 0.07))

    # Two pylons down to the bottom.
    pylon_w = int(s * 0.07)
    pylon_bottom = int(s * 0.80)
    for px in (int(s * 0.34), int(s * 0.60)):
        draw.rounded_rectangle(
            [px, deck_y, px + pylon_w, pylon_bottom],
            radius=pylon_w // 2,
            fill=_WHITE,
        )

    # --- Gemini sparkle (top-right) ---
    _draw_sparkle(draw, int(s * 0.78), int(s * 0.22), int(s * 0.11))

    # Downsample for crisp anti-aliasing.
    return tile.resize((size, size), Image.LANCZOS)


def save_png(path: str | Path, size: int = 256) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    build_image(size).save(p, format="PNG")
    return p


def save_ico(
    path: str | Path, sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    images = [build_image(sz) for sz in sizes]
    images[-1].save(p, format="ICO", sizes=[(sz, sz) for sz in sizes])
    return p


if __name__ == "__main__":
    out = save_png("icon.png", 512)
    ico = save_ico("gemini-local-bridge.ico")
    print(f"PNG -> {out}\nICO -> {ico}")