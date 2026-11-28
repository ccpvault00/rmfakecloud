from __future__ import annotations

import pymupdf
import pytest
from conftest import make_pdf, stroke

from rmsync import render


def bbox(rects):
    rects = list(rects)
    assert rects, "nothing drawn"
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


def test_stroke_lands_where_the_viewer_puts_it():
    # x is measured from the page centre in tablet units, y from the top
    pdf = make_pdf(1)
    out = pymupdf.open(stream=render.render(pdf, {"pages": [{"id": "a", "pdfPage": 0, "lines": [stroke(x=0, y=300)]}]}), filetype="pdf")
    rect = bbox(d["rect"] for d in out[0].get_drawings())
    assert rect.x0 == pytest.approx(595 / 2, abs=3)
    assert rect.y0 == pytest.approx(300 * render.PT_PER_UNIT, abs=3)
    assert rect.x1 == pytest.approx(595 / 2 + 180 * render.PT_PER_UNIT, abs=3)


def test_page_order_inserted_and_deleted_pages():
    pdf = make_pdf(3)
    ann = {"pages": [{"id": "a", "pdfPage": 2}, {"id": "b", "pdfPage": -1, "lines": [stroke()]}, {"id": "c", "pdfPage": 0}]}
    out = pymupdf.open(stream=render.render(pdf, ann), filetype="pdf")
    assert out.page_count == 3
    assert "page 3" in out[0].get_text()
    assert out[1].get_text() == "" and out[1].get_drawings()
    assert out[1].rect.width == pytest.approx(render.NOTEBOOK_WIDTH * render.PT_PER_UNIT)
    assert "page 1" in out[2].get_text()


def test_highlight_and_eraser():
    pdf = make_pdf(1)
    ann = {"pages": [{"id": "a", "pdfPage": 0, "highlights": [{"c": 9, "r": [-100, 100, 200, 20]}], "lines": [{"t": 6, "c": 0, "s": 2, "p": [0, 0, 0, 0, 0, 0] * 3}]}]}
    out = pymupdf.open(stream=render.render(pdf, ann), filetype="pdf")
    fills = [d for d in out[0].get_drawings() if d.get("fill")]
    assert len(fills) == 1
    assert len(out[0].get_drawings()) == 1  # the eraser draws nothing


def test_rotated_page_maps_to_visible_coordinates():
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).set_rotation(90)
    out = pymupdf.open(stream=render.render(doc.tobytes(), {"pages": [{"id": "a", "pdfPage": 0, "lines": [stroke(x=0, y=100)]}]}), filetype="pdf")
    page = out[0]
    rect = bbox(d["rect"] * page.rotation_matrix for d in page.get_drawings())
    assert rect.y0 == pytest.approx(100 * render.PT_PER_UNIT, abs=3)
    assert rect.x0 == pytest.approx(page.rect.width / 2, abs=3)


def test_output_content_stream_is_valid(capfd):
    pdf = make_pdf(1)
    ann = {"pages": [{"id": "a", "pdfPage": 0, "highlights": [{"c": 9, "r": [0, 0, 10, 10]}], "lines": [stroke(), {**stroke(), "t": 5}, {**stroke(), "t": 1}]}]}
    out = pymupdf.open(stream=render.render(pdf, ann), filetype="pdf")
    assert b"{" not in out[0].read_contents()
    out[0].get_pixmap()
    out[0].get_drawings()
    assert "MuPDF error" not in capfd.readouterr().out


def test_unannotated_pdf_is_returned_unchanged():
    pdf = make_pdf(2)
    assert render.render(pdf, {"pages": []}) == pdf


def test_digest_ignores_plain_page_list_but_not_reordering():
    plain = {"pages": [{"id": "a", "pdfPage": 0}, {"id": "b", "pdfPage": 1}]}
    assert render.annotation_digest(plain) is None
    assert render.annotation_digest({"pages": []}) is None
    assert render.annotation_digest({"pages": [{"id": "b", "pdfPage": 1}, {"id": "a", "pdfPage": 0}]}) is not None
    with_ink = {"pages": [{"id": "a", "pdfPage": 0, "lines": [stroke()]}]}
    assert render.annotation_digest(with_ink) != render.annotation_digest({"pages": [{"id": "a", "pdfPage": 0, "lines": [stroke(color=6)]}]})


def test_segments_match_viewer_splitting():
    s = {"t": 2, "c": 6, "s": 2, "p": [float(i) for i in range(6 * 12)]}  # ballpoint: segment every 5 points
    segs = render.segments(s)
    assert len(segs) == 3
    assert len(segs[0].points) == 5 and len(segs[1].points) == 6  # joined to the previous point
    assert segs[0].color == pytest.approx((78 / 255, 105 / 255, 201 / 255))
