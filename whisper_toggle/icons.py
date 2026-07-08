"""Generate tray icons and the multi-size app .ico."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw

Color = Tuple[int, int, int]

COLOR_IDLE = (76, 175, 80)
COLOR_RECORDING = (244, 67, 54)
COLOR_PROCESSING = (255, 193, 7)
COLOR_STARTING = (66, 165, 245)
COLOR_ERROR = (158, 158, 158)
COLOR_MIC = (255, 255, 255)


def _draw_mic_badge(size: int, fill: Color, ring: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    if ring:
        draw.ellipse([pad, pad, size - pad - 1, size - pad - 1], outline=fill, width=max(2, size // 16))
        inner = size // 5
        draw.ellipse([inner, inner, size - inner - 1, size - inner - 1], fill=fill)
    else:
        draw.ellipse([pad * 2, pad * 2, size - pad * 2 - 1, size - pad * 2 - 1], fill=fill)

    # Mic glyph (capsule + stand)
    cx, cy = size // 2, size // 2 - size // 16
    mw, mh = size // 7, size // 4
    draw.rounded_rectangle(
        [cx - mw, cy - mh, cx + mw, cy + mh // 2],
        radius=mw,
        fill=COLOR_MIC,
    )
    # Stand arc
    stand_top = cy + mh // 3
    draw.arc(
        [cx - mw * 2, stand_top - mw, cx + mw * 2, stand_top + mh],
        start=0,
        end=180,
        fill=COLOR_MIC,
        width=max(2, size // 20),
    )
    draw.line([cx, stand_top + mh - 2, cx, cy + mh], fill=COLOR_MIC, width=max(2, size // 20))
    draw.line(
        [cx - mw, cy + mh, cx + mw, cy + mh],
        fill=COLOR_MIC,
        width=max(2, size // 20),
    )
    return img


def tray_icon(state: str) -> Image.Image:
    colors = {
        "idle": COLOR_IDLE,
        "recording": COLOR_RECORDING,
        "processing": COLOR_PROCESSING,
        "starting": COLOR_STARTING,
        "error": COLOR_ERROR,
    }
    color = colors.get(state, COLOR_ERROR)
    return _draw_mic_badge(64, color, ring=(state == "recording"))


def write_app_icon(path: Path) -> Path:
    """Write a multi-size .ico used by the installer and shortcuts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    images = [_draw_mic_badge(s, COLOR_IDLE) for s in sizes]
    # Pillow saves multi-size ico from the largest with sizes=
    images[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes])
    # Also write a PNG preview
    png = path.with_suffix(".png")
    images[-1].save(png, format="PNG")
    return path
