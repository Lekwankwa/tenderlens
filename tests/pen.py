"""Pen-like edits to a scanned page: tick boxes, circle answers, write by hand, correct with fluid.

Used to make tampered packs that carry HANDWRITTEN changes a vision model has to read. The marks are drawn with a
little wobble in dark-blue ink; they are plausible, not perfect, which is the point.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

INK = (26, 30, 78)
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OBLIQUE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"


def paper(im, box, pad=14):
    x0, y0, x1, y1 = box
    ring = np.array(im.crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad)))
    return tuple(int(v) for v in np.percentile(ring.reshape(-1, 3), 90, axis=0))


def clear(im, box, pad=0):
    """Wipe an area with the page's own paper colour (what correction fluid looks like on a scan)."""
    x0, y0, x1, y1 = box
    ImageDraw.Draw(im).rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=paper(im, box))
    return im


def circle(im, box, seed=1, width=3):
    """A hand-drawn loop around an answer, closing slightly past where it started."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    rng = np.random.default_rng(seed)
    t = np.linspace(0.3, 0.3 + 2 * np.pi * 1.12, 70)
    r = 1 + 0.05 * np.sin(2.7 * t + rng.uniform(0, 6)) + np.linspace(0, 0.07, t.size) + rng.normal(0, 0.012, t.size)
    pts = [(cx + rx * k * np.cos(a), cy + ry * k * np.sin(a)) for k, a in zip(r, t)]
    ImageDraw.Draw(im).line(pts, fill=INK, width=width, joint="curve")
    return im


def cross(im, box, seed=1, width=3):
    """An X in a tick box."""
    x0, y0, x1, y1 = box
    rng = np.random.default_rng(seed)
    d = ImageDraw.Draw(im)
    j = lambda: rng.uniform(-1.5, 1.5)
    d.line([(x0 + 3 + j(), y0 + 3 + j()), (x1 - 3 + j(), y1 - 3 + j())], fill=INK, width=width)
    d.line([(x1 - 3 + j(), y0 + 3 + j()), (x0 + 3 + j(), y1 - 3 + j())], fill=INK, width=width)
    return im


def unmark_box(im, box):
    """Remove whatever was drawn in a tick box but keep the box itself."""
    x0, y0, x1, y1 = box
    bg = paper(im, (x0 - 4, y0 - 4, x1 + 4, y1 + 4), pad=10)
    d = ImageDraw.Draw(im)
    d.rectangle((x0 - 4, y0 - 4, x1 + 6, y1 + 6), fill=bg)   # box and mark together
    d.rectangle((x0, y0, x1, y1), outline=(40, 40, 40), width=2)
    return im


def hand_text(im, xy, text, size=34, seed=1, color=INK):
    """Handwriting-like text: an oblique face with each letter jittered in size, height and slant."""
    rng = np.random.default_rng(seed)
    x, y = xy
    base = ImageFont.truetype(OBLIQUE, size)
    for ch in text:
        if ch == " ":
            x += size * 0.38
            continue
        f = ImageFont.truetype(OBLIQUE, int(size * rng.uniform(0.9, 1.1)))
        tile = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((size // 2, size // 3), ch, font=f, fill=color + (255,))
        tile = tile.rotate(rng.uniform(-9, 9), resample=Image.BICUBIC, center=(size, size))
        im.paste(tile, (int(x - size // 2), int(y - size // 3 + rng.uniform(-3, 3))), tile)
        x += base.getlength(ch) * rng.uniform(0.92, 1.05)
    return im


def checkboxes(im, region):
    """Square tick boxes inside region (x0, y0, x1, y1), top to bottom, as (x, y, w, h)."""
    x0, y0, x1, y1 = region
    g = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
    bw = (g < 150).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if 15 <= w <= 38 and 15 <= h <= 38 and 0.7 < w / h < 1.4 and x0 <= x <= x1 and y0 <= y <= y1:
            out.append((x, y, w, h))
    return sorted(out, key=lambda b: (b[1], b[0]))


def strike(im, p0, p1, seed=1, width=4):
    """A hand-drawn line from p0 to p1 with a slight wobble: a strike-through."""
    rng = np.random.default_rng(seed)
    n = 26
    pts = [(p0[0] + (p1[0] - p0[0]) * i / n + rng.uniform(-1.2, 1.2),
            p0[1] + (p1[1] - p0[1]) * i / n + 4 * np.sin(i / 2.3) + rng.uniform(-1.5, 1.5)) for i in range(n + 1)]
    ImageDraw.Draw(im).line(pts, fill=INK, width=width, joint="curve")
    return im


def dots(im, x0, x1, y, step=7):
    """Redraw a dotted answer line after wiping the entry that sat on it."""
    d = ImageDraw.Draw(im)
    for x in range(x0, x1, step):
        d.ellipse((x, y, x + 2, y + 2), fill=(40, 40, 40))
    return im
