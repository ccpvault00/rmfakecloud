// Run with: node --test src/   (from ui/)
import { test } from "node:test";
import assert from "node:assert/strict";
import { PT_PER_UNIT, contentBox, pageRect, pen, readingPages, rgb, segments } from "./strokes.js";

const pt = (x, y, speed = 5, width = 12, direction = 10, pressure = 200) => [x, y, speed, width, direction, pressure];

test("ballpoint keeps its colour and splits every 5 points, joined to the previous point", () => {
  const p = [];
  for (let i = 0; i < 12; i++) p.push(...pt(i, i * 2));
  const segs = segments({ t: 15, c: 6, s: 2, p });
  assert.equal(segs.length, 3);
  assert.equal(segs[0].color, "rgb(78,105,201)");
  assert.equal(segs[0].points.length, 5);
  assert.deepEqual(segs[1].points[0], [4, 8]); // last point of the previous segment
  assert.equal(segs[1].points.length, 6);
  // 0.5 + 200/255 + 12/4 - 0.5 * (5/4/50)
  assert.ok(Math.abs(segs[0].width - (0.5 + 200 / 255 + 3 - 0.0125)) < 1e-9);
});

test("highlighter is wide, translucent and square-capped", () => {
  const [seg] = segments({ t: 18, c: 9, s: 2, p: [...pt(0, 0), ...pt(100, 0)] });
  assert.equal(seg.width, 15);
  assert.equal(seg.opacity, 0.3);
  assert.equal(seg.linecap, "square");
  assert.equal(seg.highlight, true);
});

test("eraser strokes and empty strokes draw nothing", () => {
  assert.deepEqual(segments({ t: 6, c: 0, s: 2, p: pt(1, 1) }), []);
  assert.deepEqual(segments({ t: 8, c: 0, s: 2, p: pt(1, 1) }), []);
  assert.deepEqual(segments({ t: 15, c: 0, s: 2, p: [] }), []);
});

test("a single tap becomes a dot", () => {
  const [seg] = segments({ t: 17, c: 0, s: 2, p: pt(3, 4) });
  assert.deepEqual(seg.points, [[3, 4], [3, 4]]);
  assert.equal(seg.width, 3.6);
});

test("unknown tools and colours fall back to plain black lines", () => {
  const [seg] = segments({ t: 99, c: 99, s: 2, p: [...pt(0, 0), ...pt(1, 1)] });
  assert.equal(seg.color, "rgb(0,0,0)");
  assert.equal(seg.width, 2);
  assert.equal(rgb(11), "rgb(139,208,229)");
  assert.ok(pen(99, 0, 2));
});

test("PDF pages are laid out at a fixed scale centred on x=0", () => {
  const a4 = pageRect({ width: 595, height: 842 });
  assert.ok(Math.abs(a4.w - 595 / PT_PER_UNIT) < 1e-9);
  assert.ok(Math.abs(a4.x + a4.w / 2) < 1e-9);
  assert.equal(a4.y, 0);
  // the calibration point: a highlight at x=-418.5 starts 166.4pt from the left of an A4 page
  assert.ok(Math.abs((-418.5 - a4.x) * PT_PER_UNIT - 166.4) < 2.5);
  assert.deepEqual(pageRect(null), { x: -702, y: 0, w: 1404, h: 1872 });
});

test("the content box grows to include margin notes beyond the page", () => {
  const page = pageRect(null);
  assert.deepEqual(contentBox(page, [], []), page);
  const box = contentBox(page, [{ t: 15, c: 0, s: 2, p: [...pt(1600, 1000), ...pt(-10, -50)] }], [], 24);
  assert.equal(box.x, -702);
  assert.equal(box.y, -74);
  assert.equal(box.x + box.w, 1624);
  assert.equal(box.y + box.h, 1872);
});

test("reading pages follow the server's order, or every PDF page without one", () => {
  const ann = { pages: [{ id: "b", pdfPage: 1 }, { id: "n", pdfPage: -1 }, { id: "z", pdfPage: 9 }] };
  assert.deepEqual(readingPages(ann, 3).map((p) => p.id), ["b", "n"]); // page 9 does not exist in the PDF
  assert.deepEqual(readingPages(ann, 0).map((p) => p.id), ["b", "n", "z"]); // PDF not loaded yet
  assert.deepEqual(readingPages({ pages: [] }, 2).map((p) => p.pdfPage), [0, 1]);
  assert.deepEqual(readingPages(null, 0), []);
});
