"""Draw reMarkable v6 handwriting onto a PDF, in the tablet's page order.

A Python port of the web viewer's ``ui/src/pages/Documents/strokes.js``: same pen
models, same coordinate mapping (tablet units, x from the page centre, y from the top,
``PT_PER_UNIT`` PDF points per unit). Keep the two in step.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pymupdf

log = logging.getLogger(__name__)

PT_PER_UNIT = 0.3172
NOTEBOOK_WIDTH = 1404
NOTEBOOK_HEIGHT = 1872

PALETTE: dict[int, tuple[int, int, int]] = {
    0: (0, 0, 0),
    1: (144, 144, 144),
    2: (255, 255, 255),
    3: (251, 247, 25),
    4: (0, 255, 0),
    5: (255, 192, 203),
    6: (78, 105, 201),
    7: (179, 62, 57),
    8: (125, 125, 125),
    9: (251, 247, 25),
    10: (161, 216, 125),
    11: (139, 208, 229),
    12: (183, 130, 205),
    13: (247, 232, 81),
}

WidthFn = Callable[[float, float, float, float, float], float]


@dataclass(frozen=True)
class Pen:
    """How one tool draws: width/opacity of (speed, direction, width, pressure, last width)."""

    segment_length: int
    width: WidthFn
    opacity: WidthFn
    square: bool = False


@dataclass(frozen=True)
class Segment:
    """A polyline of constant width, in tablet units."""

    points: list[tuple[float, float]]
    width: float
    opacity: float
    color: tuple[float, float, float]
    square: bool


def _tilt(direction: float) -> float:
    return direction * math.pi * 2 / 255


def _const(v: float) -> WidthFn:
    return lambda s, d, w, p, last: v


def pen(tool: int, thickness: float) -> Pen | None:
    """Pen model of a tool id (rmc's writing_tools); None for tools that draw nothing."""
    one = _const(1.0)
    if tool in (0, 12):  # paintbrush
        return Pen(2, lambda s, d, w, p, last: 0.7 * ((1 + 1.4 * p / 255) * (w / 4) - 0.5 * _tilt(d) - s / 4 / 50), one)
    if tool == 21:  # calligraphy
        return Pen(2, lambda s, d, w, p, last: 0.9 * ((1 + p / 255) * (w / 4) - 0.3 * _tilt(d)) + 0.1 * last, one)
    if tool in (3, 16):  # marker
        return Pen(3, lambda s, d, w, p, last: 0.9 * (w / 4 - 0.4 * _tilt(d)) + 0.1 * last, one)
    if tool in (2, 15):  # ballpoint
        return Pen(5, lambda s, d, w, p, last: 0.5 + p / 255 + w / 4 - 0.5 * (s / 4 / 50), one)
    if tool in (4, 17):  # fineliner
        return Pen(1000, _const(thickness * 1.8), one)
    if tool in (1, 14):  # pencil
        return Pen(
            2,
            lambda s, d, w, p, last: min(0.7 * ((0.8 * thickness + 0.5 * p / 255) * (w / 4) - 0.25 * _tilt(d) ** 1.8 - 0.6 * s / 4 / 50), thickness * 10),
            lambda s, d, w, p, last: min(max(-0.1 * (s / 4 / 35) + p / 255, 0.0), 1.0) - 0.1,
        )
    if tool in (7, 13):  # mechanical pencil
        return Pen(1000, _const(thickness**2), _const(0.7))
    if tool in (5, 18):  # highlighter
        return Pen(1000, _const(15.0), _const(0.3), square=True)
    if tool == 23:  # shader
        return Pen(1000, _const(12.0), _const(0.1))
    if tool in (6, 8):  # erasers: v6 already removed what they erased
        return None
    return Pen(1000, _const(thickness), one)


def rgb(color: int) -> tuple[float, float, float]:
    """Palette entry as 0..1 floats."""
    r, g, b = PALETTE.get(color, PALETTE[0])
    return (r / 255, g / 255, b / 255)


def segments(stroke: dict[str, Any]) -> list[Segment]:
    """Split a stroke into constant-width polylines, joining each to the previous point."""
    style = pen(int(stroke.get("t", 0)), float(stroke.get("s", 2.0)))
    p: Sequence[float] = stroke.get("p") or []
    n = len(p) // 6
    if style is None or n == 0:
        return []
    color = rgb(int(stroke.get("c", 0)))
    out: list[Segment] = []
    last_width = 0.0
    prev: tuple[float, float] | None = None
    for i in range(n):
        x, y, speed, width, direction, pressure = p[i * 6 : i * 6 + 6]
        if i % style.segment_length == 0:
            w = max(style.width(speed, direction, width, pressure, last_width), 0.5)
            op = max(style.opacity(speed, direction, width, pressure, last_width), 0.0)
            out.append(Segment([prev] if prev else [], w, op, color, style.square))
            last_width = w
        prev = (x, y)
        out[-1].points.append(prev)
    for seg in out:
        if len(seg.points) == 1:
            seg.points.append(seg.points[0])
    return out


def annotation_digest(ann: dict[str, Any]) -> str | None:
    """Digest of what write-back would draw; None when there is nothing to draw.

    The page list alone (all PDF pages in order, no strokes) is what a document looks like
    right after upload, so it counts as nothing; deleted, moved or inserted pages count.
    """
    pages = ann.get("pages") or []
    order = [int(pg.get("pdfPage", -1)) for pg in pages]
    content = [[pg.get("pdfPage"), pg.get("lines") or [], pg.get("highlights") or []] for pg in pages if pg.get("lines") or pg.get("highlights")]
    identity = order == list(range(len(order)))
    if not content and identity:
        return None
    blob = json.dumps({"order": order, "content": content}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _draw_page(page: pymupdf.Page, lines: Sequence[dict[str, Any]], highlights: Sequence[dict[str, Any]]) -> None:
    rect = page.rect
    half_w = rect.width / 2
    to_unrotated = page.derotation_matrix

    def pt(x: float, y: float) -> pymupdf.Point:
        point: pymupdf.Point = pymupdf.Point(x * PT_PER_UNIT + half_w, y * PT_PER_UNIT) * to_unrotated
        return point

    shape = page.new_shape()
    for h in highlights:
        r = h.get("r") or []
        for i in range(0, len(r) - 3, 4):
            x, y, w, hh = r[i : i + 4]
            shape.draw_quad(pymupdf.Quad(pt(x, y), pt(x + w, y), pt(x, y + hh), pt(x + w, y + hh)))
            shape.finish(color=None, fill=rgb(int(h.get("c", 9))), fill_opacity=0.35, width=0)
    for stroke in lines:
        for seg in segments(stroke):
            if seg.opacity <= 0:
                continue
            shape.draw_polyline([pt(x, y) for x, y in seg.points])
            shape.finish(
                color=seg.color,
                width=seg.width * PT_PER_UNIT,
                # no lineJoin: PyMuPDF 1.27.2 writes a non-zero one as the literal "{lineJoin} j"
                lineCap=2 if seg.square else 1,
                stroke_opacity=min(seg.opacity, 1.0),
                closePath=False,
            )
    shape.commit(overlay=True)


def render(pdf: bytes | None, ann: dict[str, Any]) -> bytes:
    """The document as the tablet shows it: PDF pages in tablet order, inserted blank pages, handwriting.

    Args:
        pdf: The original PDF, or None for a notebook.
        ann: The server's ``/annotations`` reply.

    Returns:
        The rendered PDF. Writing beyond the page edge is clipped by the page.
    """
    src = pymupdf.open(stream=pdf, filetype="pdf") if pdf else pymupdf.open()
    pages = ann.get("pages") or []
    if not pages:
        if pdf is None:
            raise ValueError("notebook without pages")
        return pdf
    if any(pg.get("hasText") for pg in pages):
        log.warning("typed text on the tablet is not rendered")

    order = [int(pg.get("pdfPage", -1)) for pg in pages]
    in_place = pdf is not None and order == list(range(src.page_count))
    if in_place:
        out = src
    else:
        out = pymupdf.open()
        nb_size = (NOTEBOOK_WIDTH * PT_PER_UNIT, NOTEBOOK_HEIGHT * PT_PER_UNIT)
        for idx in order:
            if 0 <= idx < src.page_count:
                out.insert_pdf(src, from_page=idx, to_page=idx)
            else:
                out.new_page(width=nb_size[0], height=nb_size[1])
    for i, pg in enumerate(pages):
        if pg.get("lines") or pg.get("highlights"):
            _draw_page(out[i], pg.get("lines") or [], pg.get("highlights") or [])
    data: bytes = out.tobytes(garbage=1, deflate=True)
    return data
