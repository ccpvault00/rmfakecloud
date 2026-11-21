import { useMemo } from "react";
import { Page } from "react-pdf";
import { contentBox, pageRect, rgb, segments } from "./strokes";
import styles from "./Viewer.module.scss";

// One page: the PDF page (or a blank notebook page) with the handwriting drawn over it
// as SVG. The box grows when there is writing beyond the page edge.
export default function AnnotatedPage({ page, pdfSize, availWidth, availHeight, fitWidth, zoom, onPdfSize }) {
  const hasPdf = page.pdfPage >= 0;
  const rect = pageRect(hasPdf ? pdfSize : null);
  const box = contentBox(rect, page.lines, page.highlights);

  const fit = fitWidth ? availWidth / box.w : Math.min(availWidth / box.w, availHeight / box.h);
  const k = Math.max(fit, 0.01) * zoom; // css px per tablet unit

  const strokes = useMemo(() => (page.lines || []).flatMap((l) => segments(l)), [page.lines]);

  return (
    <div className={styles.sheet} style={{ width: box.w * k, height: box.h * k }}>
      <div
        className={styles.paper}
        style={{ left: (rect.x - box.x) * k, top: (rect.y - box.y) * k, width: rect.w * k, height: rect.h * k }}
      >
        {hasPdf && (
          <Page
            pageNumber={page.pdfPage + 1}
            width={rect.w * k}
            renderTextLayer={false}
            renderAnnotationLayer={true}
            onLoadSuccess={(p) => onPdfSize({ width: p.originalWidth, height: p.originalHeight })}
          />
        )}
      </div>
      <svg className={styles.ink} viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} preserveAspectRatio="none">
        {(page.highlights || []).map((h, i) => {
          const r = h.r || [];
          const rects = [];
          for (let j = 0; j + 3 < r.length; j += 4) {
            rects.push(<rect key={j} x={r[j]} y={r[j + 1]} width={r[j + 2]} height={r[j + 3]} />);
          }
          return (
            <g key={`h${i}`} fill={rgb(h.c)} opacity={0.45} className={styles.marker}>
              {rects}
            </g>
          );
        })}
        {strokes.map((s, i) => (
          <polyline
            key={i}
            points={s.points.map((p) => `${p[0]},${p[1]}`).join(" ")}
            fill="none"
            stroke={s.color}
            strokeWidth={s.width}
            strokeOpacity={s.opacity}
            strokeLinecap={s.linecap}
            strokeLinejoin="round"
            className={s.highlight ? styles.marker : undefined}
          />
        ))}
      </svg>
    </div>
  );
}
