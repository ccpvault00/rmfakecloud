package models

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path"
	"sort"
	"strings"

	"github.com/ddvk/rmfakecloud/internal/storage"
	"github.com/ddvk/rmfakecloud/internal/storage/rmv6"
	log "github.com/sirupsen/logrus"
)

// ErrNoPayload is returned when a document has no PDF to show (a notebook, or an
// EPUB the tablet has not converted yet).
var ErrNoPayload = errors.New("document has no pdf")

// DocAnnotations is what the web viewer needs to draw a document: the pages in
// reading order, which PDF page each one shows and the handwriting on it.
type DocAnnotations struct {
	FileType string            `json:"fileType"` // pdf, epub or notebook
	HasPDF   bool              `json:"hasPdf"`
	Pages    []PageAnnotations `json:"pages"`
}

// PageAnnotations is one page; coordinates are tablet units (x from the page centre,
// y from the top), so the client applies one scale for PDF pages and notebooks alike.
type PageAnnotations struct {
	ID         string          `json:"id"`
	PDFPage    int             `json:"pdfPage"` // 0-based; -1 for an inserted blank page
	Lines      []StrokeJSON    `json:"lines,omitempty"`
	Highlights []HighlightJSON `json:"highlights,omitempty"`
	HasText    bool            `json:"hasText,omitempty"`
}

// StrokeJSON is a compact stroke: P holds x, y, speed, width, direction, pressure per point.
type StrokeJSON struct {
	Tool      uint32    `json:"t"`
	Color     uint32    `json:"c"`
	Thickness float64   `json:"s"`
	P         []float32 `json:"p"`
}

// HighlightJSON is a text highlight; R holds x, y, w, h per rectangle.
type HighlightJSON struct {
	Color uint32    `json:"c"`
	R     []float64 `json:"r"`
}

// pageList is the part of .content that orders pages: cPages (formatVersion 2,
// software 3.x) or pages + redirectionPageMap (formatVersion 1).
type pageList struct {
	FileType string   `json:"fileType"`
	Pages    []string `json:"pages"`
	Redirect []int    `json:"redirectionPageMap"`
	CPages   struct {
		Pages []struct {
			ID  string `json:"id"`
			Idx struct {
				Value string `json:"value"`
			} `json:"idx"`
			Redir *struct {
				Value int `json:"value"`
			} `json:"redir"`
			Deleted *struct {
				Value int `json:"value"`
			} `json:"deleted"`
		} `json:"pages"`
	} `json:"cPages"`
}

type pageRef struct {
	id      string
	pdfPage int
}

func (p *pageList) ordered() []pageRef {
	if len(p.CPages.Pages) > 0 {
		pages := p.CPages.Pages
		sort.SliceStable(pages, func(i, j int) bool { return pages[i].Idx.Value < pages[j].Idx.Value })
		refs := make([]pageRef, 0, len(pages))
		for _, cp := range pages {
			if cp.Deleted != nil && cp.Deleted.Value != 0 {
				continue
			}
			ref := pageRef{id: cp.ID, pdfPage: -1}
			if cp.Redir != nil {
				ref.pdfPage = cp.Redir.Value
			}
			refs = append(refs, ref)
		}
		return refs
	}
	refs := make([]pageRef, 0, len(p.Pages))
	for i, id := range p.Pages {
		ref := pageRef{id: id, pdfPage: i}
		if len(p.Redirect) == len(p.Pages) {
			ref.pdfPage = p.Redirect[i]
		}
		refs = append(refs, ref)
	}
	return refs
}

func readBlob(rs RemoteStorage, hash string) ([]byte, error) {
	r, err := rs.GetReader(hash)
	if err != nil {
		return nil, err
	}
	defer r.Close()
	return io.ReadAll(r)
}

// AnnotationsFromHashDoc decodes the page order and the v6 handwriting of a document.
func AnnotationsFromHashDoc(doc *HashDoc, rs RemoteStorage) (*DocAnnotations, error) {
	var list pageList
	rmFiles := map[string]string{} // page id -> blob hash
	out := &DocAnnotations{}
	for _, f := range doc.Files {
		ext := path.Ext(f.EntryName)
		switch ext {
		case storage.ContentFileExt:
			data, err := readBlob(rs, f.Hash)
			if err != nil {
				return nil, err
			}
			if err := json.Unmarshal(data, &list); err != nil {
				return nil, fmt.Errorf("content of %s: %w", doc.EntryName, err)
			}
		case storage.PdfFileExt:
			out.HasPDF = true
		case storage.RmFileExt:
			rmFiles[strings.TrimSuffix(path.Base(f.EntryName), ext)] = f.Hash
		}
	}

	out.FileType = list.FileType
	if out.FileType == "" {
		out.FileType = "notebook"
	}
	refs := list.ordered()
	if len(refs) == 0 && out.FileType == "notebook" {
		// a notebook without a page list: show its pages in id order
		for id := range rmFiles {
			refs = append(refs, pageRef{id: id, pdfPage: -1})
		}
		sort.Slice(refs, func(i, j int) bool { return refs[i].id < refs[j].id })
	}

	out.Pages = make([]PageAnnotations, 0, len(refs))
	for _, ref := range refs {
		page := PageAnnotations{ID: ref.id, PDFPage: ref.pdfPage}
		if hash, ok := rmFiles[ref.id]; ok {
			if err := addScene(&page, rs, hash); err != nil {
				// one unreadable page must not hide the rest of the document
				log.Warnf("annotations %s page %s: %v", doc.EntryName, ref.id, err)
			}
		}
		out.Pages = append(out.Pages, page)
	}
	return out, nil
}

func addScene(page *PageAnnotations, rs RemoteStorage, hash string) error {
	data, err := readBlob(rs, hash)
	if err != nil {
		return err
	}
	scene, err := rmv6.Parse(data)
	if err != nil {
		return err
	}
	if scene.Skipped > 0 {
		log.Warnf("annotations page %s: skipped %d undecodable blocks", page.ID, scene.Skipped)
	}
	page.HasText = scene.HasText
	for _, l := range scene.Lines {
		s := StrokeJSON{Tool: l.Tool, Color: l.Color, Thickness: l.ThicknessScale, P: make([]float32, 0, 6*len(l.Points))}
		for _, p := range l.Points {
			s.P = append(s.P, p.X, p.Y, float32(p.Speed), float32(p.Width), float32(p.Direction), float32(p.Pressure))
		}
		page.Lines = append(page.Lines, s)
	}
	for _, h := range scene.Highlights {
		hl := HighlightJSON{Color: h.Color, R: make([]float64, 0, 4*len(h.Rects))}
		for _, q := range h.Rects {
			hl.R = append(hl.R, q.X, q.Y, q.W, q.H)
		}
		page.Highlights = append(page.Highlights, hl)
	}
	return nil
}

// PDFFromHashDoc opens the document's PDF as the tablet shows it: the original PDF,
// or for an EPUB the PDF rendition the tablet generated.
func PDFFromHashDoc(doc *HashDoc, rs RemoteStorage) (io.ReadCloser, error) {
	for _, f := range doc.Files {
		if path.Ext(f.EntryName) == storage.PdfFileExt {
			return rs.GetReader(f.Hash)
		}
	}
	return nil, ErrNoPayload
}
