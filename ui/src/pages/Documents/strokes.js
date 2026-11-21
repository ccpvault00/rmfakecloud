// Drawing of reMarkable v6 strokes for the document viewer.
//
// Coordinates are tablet units: x from the page centre, y from the top. Pen widths and
// colours follow rmc's writing_tools (MIT, https://github.com/ricklupton/rmc), except that
// ballpoint keeps its colour (rmc greys it out, but colour tablets write blue/red ballpoint).

// PDF points per tablet unit. Calibrated on highlights of real documents (A4, US letter
// and EPUB renditions): the tablet lays PDF pages out at a fixed ~227 units per inch,
// not scaled to the page size.
export const PT_PER_UNIT = 0.3172;

// A notebook page in tablet units.
export const NOTEBOOK_WIDTH = 1404;
export const NOTEBOOK_HEIGHT = 1872;

const PALETTE = {
  0: [0, 0, 0], // black
  1: [144, 144, 144], // gray
  2: [255, 255, 255], // white
  3: [251, 247, 25], // yellow
  4: [0, 255, 0], // green
  5: [255, 192, 203], // pink
  6: [78, 105, 201], // blue
  7: [179, 62, 57], // red
  8: [125, 125, 125], // gray overlap
  9: [251, 247, 25], // highlight (all highlighter colours share this id)
  10: [161, 216, 125], // green 2
  11: [139, 208, 229], // cyan
  12: [183, 130, 205], // magenta
  13: [247, 232, 81], // yellow 2
};

const tilt = (direction) => (direction * Math.PI * 2) / 255;
const clamp01 = (v) => Math.min(Math.max(v, 0), 1);

export function rgb(color) {
  const c = PALETTE[color] || PALETTE[0];
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

// pen describes how one tool draws; width/opacity get (speed, direction, width, pressure, lastWidth)
export function pen(tool, color, thickness) {
  const base = {
    segmentLength: 1000,
    linecap: "round",
    color: rgb(color),
    width: () => thickness,
    opacity: () => 1,
  };
  switch (tool) {
    case 0: // paintbrush 1
    case 12: // paintbrush 2
      return {
        ...base,
        segmentLength: 2,
        width: (s, d, w, p) => 0.7 * ((1 + (1.4 * p) / 255) * (w / 4) - 0.5 * tilt(d) - s / 4 / 50),
      };
    case 21: // calligraphy
      return {
        ...base,
        segmentLength: 2,
        width: (s, d, w, p, last) => 0.9 * ((1 + p / 255) * (w / 4) - 0.3 * tilt(d)) + 0.1 * last,
      };
    case 3: // marker 1
    case 16: // marker 2
      return {
        ...base,
        segmentLength: 3,
        width: (s, d, w, p, last) => 0.9 * (w / 4 - 0.4 * tilt(d)) + 0.1 * last,
      };
    case 2: // ballpoint 1
    case 15: // ballpoint 2
      return {
        ...base,
        segmentLength: 5,
        width: (s, d, w, p) => 0.5 + p / 255 + w / 4 - 0.5 * (s / 4 / 50),
      };
    case 4: // fineliner 1
    case 17: // fineliner 2
      return { ...base, width: () => thickness * 1.8 };
    case 1: // pencil 1
    case 14: // pencil 2
      return {
        ...base,
        segmentLength: 2,
        width: (s, d, w, p) =>
          Math.min(0.7 * ((0.8 * thickness + (0.5 * p) / 255) * (w / 4) - 0.25 * tilt(d) ** 1.8 - (0.6 * s) / 4 / 50), thickness * 10),
        opacity: (s, d, w, p) => clamp01(-0.1 * (s / 4 / 35) + p / 255) - 0.1,
      };
    case 7: // mechanical pencil 1
    case 13: // mechanical pencil 2
      return { ...base, width: () => thickness ** 2, opacity: () => 0.7 };
    case 5: // highlighter 1
    case 18: // highlighter 2
      return { ...base, linecap: "square", width: () => 15, opacity: () => 0.3, highlight: true };
    case 23: // shader
      return { ...base, width: () => 12, opacity: () => 0.1 };
    case 6: // eraser: v6 deletes erased strokes, an eraser stroke itself draws nothing
    case 8: // erase area
      return null;
    default:
      return base;
  }
}

// segments splits a stroke ({t, c, s, p: [x, y, speed, width, direction, pressure, ...]})
// into polylines of constant width, as rmc does, joining each to the previous point.
export function segments(stroke) {
  const style = pen(stroke.t, stroke.c, stroke.s);
  const p = stroke.p || [];
  const n = Math.floor(p.length / 6);
  if (!style || n === 0) return [];
  const out = [];
  let current = null;
  let lastWidth = 0;
  let prev = null;
  for (let i = 0; i < n; i++) {
    const [x, y, speed, width, direction, pressure] = p.slice(i * 6, i * 6 + 6);
    if (i % style.segmentLength === 0) {
      const w = Math.max(style.width(speed, direction, width, pressure, lastWidth), 0.5);
      current = {
        points: prev ? [prev] : [],
        width: w,
        opacity: Math.max(style.opacity(speed, direction, width, pressure, lastWidth), 0),
        color: style.color,
        linecap: style.linecap,
        highlight: !!style.highlight,
      };
      out.push(current);
      lastWidth = w;
    }
    prev = [x, y];
    current.points.push(prev);
  }
  // a single tap still shows as a dot
  for (const seg of out) if (seg.points.length === 1) seg.points.push(seg.points[0]);
  return out;
}

// pageRect is the page itself in tablet units; size is the PDF page in points, or null
// for a page without PDF background (notebook or inserted page).
export function pageRect(size) {
  if (!size) return { x: -NOTEBOOK_WIDTH / 2, y: 0, w: NOTEBOOK_WIDTH, h: NOTEBOOK_HEIGHT };
  const w = size.width / PT_PER_UNIT;
  const h = size.height / PT_PER_UNIT;
  return { x: -w / 2, y: 0, w, h };
}

// contentBox is the page grown to include writing in the margins (the tablet lets you
// write beyond the page edge), with a small border around such writing.
export function contentBox(page, lines, highlights, margin = 24) {
  let x0 = page.x;
  let y0 = page.y;
  let x1 = page.x + page.w;
  let y1 = page.y + page.h;
  const grow = (x, y, pad) => {
    x0 = Math.min(x0, x - pad);
    y0 = Math.min(y0, y - pad);
    x1 = Math.max(x1, x + pad);
    y1 = Math.max(y1, y + pad);
  };
  for (const l of lines || []) {
    const p = l.p || [];
    for (let i = 0; i + 1 < p.length; i += 6) grow(p[i], p[i + 1], margin);
  }
  for (const h of highlights || []) {
    const r = h.r || [];
    for (let i = 0; i + 3 < r.length; i += 4) {
      grow(r[i], r[i + 1], 0);
      grow(r[i] + r[i + 2], r[i + 1] + r[i + 3], 0);
    }
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// readingPages maps the server's page list (or, without one, every PDF page) to the
// pages the viewer steps through: {pdfPage (0-based, -1 = none), annotations}.
export function readingPages(annotations, numPdfPages) {
  const pages = annotations && annotations.pages && annotations.pages.length ? annotations.pages : null;
  if (pages) {
    return pages.filter((p) => p.pdfPage < 0 || !numPdfPages || p.pdfPage < numPdfPages);
  }
  return Array.from({ length: numPdfPages || 0 }, (_, i) => ({ id: `pdf-${i}`, pdfPage: i }));
}
