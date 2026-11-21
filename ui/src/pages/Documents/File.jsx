import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Button, ButtonGroup, Dropdown, Form } from "react-bootstrap";
import Navbar from "react-bootstrap/Navbar";
import { FaChevronRight, FaChevronLeft, FaExpand, FaCompress, FaMagnifyingGlassPlus, FaMagnifyingGlassMinus, FaArrowsLeftRight } from "react-icons/fa6";
import { AiOutlineDownload } from "react-icons/ai";
import { pdfjs, Document } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";

import constants from "../../common/constants";
import apiservice from "../../services/api.service";
import NameTag from "../../components/NameTag";
import AnnotatedPage from "./AnnotatedPage";
import { readingPages } from "./strokes";
import styles from "./Viewer.module.scss";

const ZOOMS = [1, 1.25, 1.5, 2, 3];
const SWIPE_PX = 60;

export default function FileViewer({ file, onSelect }) {
  const { data } = file;
  const pdfUrl = `${constants.ROOT_URL}/documents/${file.id}/pdf`;

  const [annotations, setAnnotations] = useState(null);
  const [annError, setAnnError] = useState(null);
  const [numPdfPages, setNumPdfPages] = useState(0);
  const [pdfError, setPdfError] = useState(null);
  const [idx, setIdx] = useState(0);
  const [pdfSizes, setPdfSizes] = useState({});
  const [fullscreen, setFullscreen] = useState(false);
  const [fitWidth, setFitWidth] = useState(false);
  const [zoomStep, setZoomStep] = useState(0);
  const [avail, setAvail] = useState({ width: 300, height: 300 });
  const [pageText, setPageText] = useState("1");

  const viewerRef = useRef(null);
  const stageRef = useRef(null);
  const touch = useRef(null);

  // a new document starts at its first page
  useEffect(() => {
    setAnnotations(null);
    setAnnError(null);
    setNumPdfPages(0);
    setPdfError(null);
    setIdx(0);
    setPdfSizes({});
    const ctrl = new AbortController();
    fetch(`${constants.ROOT_URL}/documents/${file.id}/annotations`, { signal: ctrl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setAnnotations)
      .catch((e) => {
        if (e.name !== "AbortError") setAnnError(e.message);
      });
    return () => ctrl.abort();
  }, [file.id]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setAvail({ width: el.clientWidth - 16, height: el.clientHeight - 16 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // if the handwriting cannot be loaded, still show the PDF page by page
  const showPdf = annotations ? annotations.hasPdf : !!annError;
  const pages = useMemo(() => readingPages(annotations, numPdfPages), [annotations, numPdfPages]);
  const total = pages.length;
  const current = pages[Math.min(idx, Math.max(total - 1, 0))];

  useEffect(() => setPageText(String(Math.min(idx, Math.max(total - 1, 0)) + 1)), [idx, total]);

  const go = useCallback(
    (n) => {
      setIdx((i) => Math.min(Math.max(typeof n === "function" ? n(i) : n, 0), Math.max(total - 1, 0)));
      if (stageRef.current) stageRef.current.scrollTo(0, 0);
    },
    [total],
  );
  const prev = () => go((i) => i - 1);
  const next = () => go((i) => i + 1);

  // full screen: real fullscreen where the browser supports it, the fixed overlay always
  const enterFullscreen = () => {
    setFullscreen(true);
    setFitWidth(true);
    const el = viewerRef.current;
    if (el && el.requestFullscreen) el.requestFullscreen().catch(() => {});
  };
  const exitFullscreen = () => {
    setFullscreen(false);
    setFitWidth(false);
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
  };
  useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) {
        setFullscreen(false);
        setFitWidth(false);
      }
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // A sideways swipe the page cannot scroll is a page turn here, but Chrome turns it into
  // "go back" and leaves the document. Cancel such moves (only at the scroll edge, so a
  // zoomed page still pans); needs a non-passive listener, which React does not attach.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onMove = (e) => {
      const t = touch.current;
      if (!t || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - t.x;
      const dy = e.touches[0].clientY - t.y;
      if (Math.abs(dx) <= Math.abs(dy)) return;
      const atLeft = el.scrollLeft <= 1;
      const atRight = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
      if ((dx > 0 && atLeft) || (dx < 0 && atRight)) e.preventDefault();
    };
    el.addEventListener("touchmove", onMove, { passive: false });
    return () => el.removeEventListener("touchmove", onMove);
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target && (e.target.tagName === "INPUT" || e.target.isContentEditable)) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") go((i) => i + 1);
      else if (e.key === "ArrowLeft" || e.key === "PageUp") go((i) => i - 1);
      else if (e.key === "Escape" && fullscreen) exitFullscreen();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, fullscreen]);

  // horizontal swipe turns the page, unless the page is zoomed and can still scroll that way
  const onTouchStart = (e) => {
    if (e.touches.length !== 1) {
      touch.current = null;
      return;
    }
    const el = stageRef.current;
    touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, left: el.scrollLeft };
  };
  const onTouchEnd = (e) => {
    const t = touch.current;
    touch.current = null;
    if (!t || e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - t.x;
    const dy = e.changedTouches[0].clientY - t.y;
    if (Math.abs(dx) < SWIPE_PX || Math.abs(dy) > Math.abs(dx) / 1.5) return;
    const el = stageRef.current;
    const atLeft = t.left <= 1;
    const atRight = t.left + el.clientWidth >= el.scrollWidth - 1;
    if (dx < 0 && atRight) next();
    else if (dx > 0 && atLeft) prev();
  };

  const onItemClick = ({ pageNumber }) => {
    const target = pages.findIndex((p) => p.pdfPage === pageNumber - 1);
    if (target >= 0) go(target);
  };

  const triggerDownload = (blob, filename) => {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  const onDownloadPdf = () => {
    apiservice
      .download(data.id)
      .then((blob) => triggerDownload(blob, data.name + ".pdf"))
      .catch(() => {});
  };
  const onDownloadRmdoc = () => {
    apiservice
      .download(data.id, "rmdoc")
      .then((blob) => triggerDownload(blob, data.name + ".rmdoc"))
      .catch(() => {});
  };

  // fetch only the byte ranges of the pages shown: PDFs can be hundreds of MB
  const options = useMemo(() => ({ worker: new pdfjs.PDFWorker(), disableAutoFetch: true, disableStream: true }), []);

  const zoom = ZOOMS[zoomStep];
  const knownSize = current && current.pdfPage >= 0 ? pdfSizes[current.pdfPage] || Object.values(pdfSizes)[0] : null;
  const pageSize = knownSize || { width: 595, height: 842 };

  const commitPageText = () => {
    const n = parseInt(pageText, 10);
    if (!Number.isNaN(n)) go(n - 1);
    else setPageText(String(idx + 1));
  };

  const notReady = annotations && annotations.fileType === "epub" && !annotations.hasPdf;

  const page = current && (
    <AnnotatedPage
      page={current}
      pdfSize={pageSize}
      availWidth={avail.width}
      availHeight={avail.height}
      fitWidth={fitWidth}
      zoom={zoom}
      onPdfSize={(size) =>
        setPdfSizes((s) => (s[current.pdfPage] && s[current.pdfPage].width === size.width ? s : { ...s, [current.pdfPage]: size }))
      }
    />
  );

  return (
    <div ref={viewerRef} className={`${styles.viewer} ${fullscreen ? styles.fullscreen : ""}`}>
      {!fullscreen && (
        <Navbar style={{ marginLeft: "-12px", flexShrink: 0 }}>{file && <div><NameTag node={file} onSelect={onSelect} /></div>}</Navbar>
      )}

      <div className={styles.toolbar}>
        <ButtonGroup>
          <Button size="sm" variant="outline-secondary" onClick={prev} disabled={idx <= 0} aria-label="Previous page">
            <FaChevronLeft />
          </Button>
          <Button size="sm" variant="outline-secondary" onClick={next} disabled={idx >= total - 1} aria-label="Next page">
            <FaChevronRight />
          </Button>
        </ButtonGroup>
        <span className={styles.pageCounter}>
          <Form.Control
            size="sm"
            className={styles.pageInput}
            value={pageText}
            inputMode="numeric"
            aria-label="Page"
            onChange={(e) => setPageText(e.target.value)}
            onBlur={commitPageText}
            onKeyDown={(e) => e.key === "Enter" && commitPageText()}
          />
          / {total || "…"}
        </span>
        <div className={styles.spacer} />
        <ButtonGroup>
          <Button size="sm" variant="outline-secondary" onClick={() => setZoomStep((z) => Math.max(z - 1, 0))} disabled={zoomStep === 0} aria-label="Zoom out">
            <FaMagnifyingGlassMinus />
          </Button>
          <Button size="sm" variant="outline-secondary" onClick={() => setZoomStep((z) => Math.min(z + 1, ZOOMS.length - 1))} disabled={zoomStep === ZOOMS.length - 1} aria-label="Zoom in">
            <FaMagnifyingGlassPlus />
          </Button>
          <Button size="sm" variant={fitWidth ? "secondary" : "outline-secondary"} onClick={() => setFitWidth((f) => !f)} aria-label="Fit width">
            <FaArrowsLeftRight />
          </Button>
        </ButtonGroup>
        <Button size="sm" variant="outline-secondary" onClick={fullscreen ? exitFullscreen : enterFullscreen} aria-label={fullscreen ? "Exit full screen" : "Full screen"}>
          {fullscreen ? <FaCompress /> : <FaExpand />}
        </Button>
        {!fullscreen && (
          <Dropdown align="end">
            <Dropdown.Toggle size="sm" variant="secondary">
              <AiOutlineDownload />
            </Dropdown.Toggle>
            <Dropdown.Menu>
              <Dropdown.Item onClick={onDownloadPdf}>Download PDF</Dropdown.Item>
              <Dropdown.Item onClick={onDownloadRmdoc}>Download .rmdoc</Dropdown.Item>
            </Dropdown.Menu>
          </Dropdown>
        )}
      </div>

      <div ref={stageRef} className={styles.stage} onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
        {annError && <div className={styles.message}>Handwriting could not be loaded ({annError}).</div>}
        {current && current.hasText && <div className={styles.message}>This page has typed text, which is not shown.</div>}
        {notReady && <div className={styles.message}>Open this EPUB once on the tablet so it creates the pages shown here, or download the .rmdoc.</div>}
        {pdfError && <div className={styles.message}>Failed to load the PDF ({pdfError}).</div>}
        <div className={styles.stageInner}>
          {showPdf ? (
            <Document
              file={pdfUrl}
              options={options}
              onLoadSuccess={(pdf) => setNumPdfPages(pdf.numPages)}
              onLoadError={(e) => setPdfError(e.message)}
              onItemClick={onItemClick}
              externalLinkTarget="_blank"
              loading={<div className={styles.message}>Loading…</div>}
            >
              {page}
            </Document>
          ) : (
            annotations && !notReady && page
          )}
        </div>
      </div>
    </div>
  );
}
