"""Tray icon rendering: a coloured disc with a microphone or headphones glyph."""

from functools import lru_cache

from PIL import Image, ImageDraw

COLORS = {
    "live": (46, 160, 67),       # connected, mic open
    "muted": (218, 54, 51),      # connected, mic muted or deafened
    "warning": (230, 140, 20),   # GoXLR Utility or Discord unreachable
    "idle": (120, 124, 135),     # setup or authorization required
}

_SCALE = 4  # draw large, then downsample for anti-aliasing


def _mic(draw: ImageDraw.ImageDraw, s: int, w: int) -> None:
    white = (255, 255, 255, 255)
    draw.rounded_rectangle([s * 0.40, s * 0.20, s * 0.60, s * 0.56], radius=s * 0.10, fill=white)
    draw.arc([s * 0.30, s * 0.32, s * 0.70, s * 0.66], start=0, end=180, fill=white, width=w)
    draw.line([s * 0.50, s * 0.66, s * 0.50, s * 0.76], fill=white, width=w)
    draw.line([s * 0.38, s * 0.77, s * 0.62, s * 0.77], fill=white, width=w)


def _headphones(draw: ImageDraw.ImageDraw, s: int, w: int) -> None:
    white = (255, 255, 255, 255)
    draw.arc([s * 0.24, s * 0.22, s * 0.76, s * 0.74], start=180, end=360, fill=white, width=w)
    draw.line([s * 0.25, s * 0.48, s * 0.25, s * 0.60], fill=white, width=w)
    draw.line([s * 0.75, s * 0.48, s * 0.75, s * 0.60], fill=white, width=w)
    draw.rounded_rectangle([s * 0.22, s * 0.52, s * 0.36, s * 0.76], radius=s * 0.04, fill=white)
    draw.rounded_rectangle([s * 0.64, s * 0.52, s * 0.78, s * 0.76], radius=s * 0.04, fill=white)


@lru_cache(maxsize=32)
def render(color: str, glyph: str = "mic", slashed: bool = False, size: int = 64) -> Image.Image:
    s = size * _SCALE
    w = max(2, int(s * 0.06))
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse([s * 0.02, s * 0.02, s * 0.98, s * 0.98], fill=COLORS[color] + (255,))
    (_headphones if glyph == "headphones" else _mic)(draw, s, w)
    if slashed:
        bg = COLORS[color] + (255,)
        draw.line([s * 0.24, s * 0.20, s * 0.80, s * 0.84], fill=bg, width=w * 3)
        draw.line([s * 0.24, s * 0.20, s * 0.80, s * 0.84], fill=(255, 255, 255, 255), width=w)
    return image.resize((size, size), Image.LANCZOS)


def save_ico(path) -> None:
    """Application icon for the executables."""
    render("live", "mic", False, 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
