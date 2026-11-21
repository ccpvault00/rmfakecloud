package models

import (
	"errors"
	"io"
	"os"
	"testing"
)

func annDoc(files map[string][]byte) (*HashDoc, memStorage) {
	rs := memStorage{}
	doc := &HashDoc{HashEntry: HashEntry{EntryName: "doc"}}
	for name, data := range files {
		rs[name] = data
		doc.Files = append(doc.Files, &HashEntry{Hash: name, EntryName: name})
	}
	return doc, rs
}

func synthetic(t *testing.T) []byte {
	t.Helper()
	b, err := os.ReadFile("../rmv6/testdata/synthetic.rm")
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestAnnotations_CPagesOrderRedirAndDeleted(t *testing.T) {
	// idx order b < c < d, page "x" deleted, page "c" inserted (no redir)
	content := `{"fileType":"pdf","formatVersion":2,"cPages":{"pages":[
		{"id":"d","idx":{"value":"d"},"redir":{"value":1}},
		{"id":"x","idx":{"value":"a"},"redir":{"value":5},"deleted":{"value":1}},
		{"id":"b","idx":{"value":"b"},"redir":{"value":0}},
		{"id":"c","idx":{"value":"c"}}]}}`
	doc, rs := annDoc(map[string][]byte{
		"doc.content": []byte(content),
		"doc.pdf":     []byte("%PDF"),
		"doc/d.rm":    synthetic(t),
	})
	ann, err := AnnotationsFromHashDoc(doc, rs)
	if err != nil {
		t.Fatal(err)
	}
	if ann.FileType != "pdf" || !ann.HasPDF {
		t.Errorf("fileType=%q hasPdf=%v", ann.FileType, ann.HasPDF)
	}
	want := []struct {
		id      string
		pdfPage int
		lines   int
	}{{"b", 0, 0}, {"c", -1, 0}, {"d", 1, 3}}
	if len(ann.Pages) != len(want) {
		t.Fatalf("pages %+v", ann.Pages)
	}
	for i, w := range want {
		p := ann.Pages[i]
		if p.ID != w.id || p.PDFPage != w.pdfPage || len(p.Lines) != w.lines {
			t.Errorf("page %d = %s pdf %d lines %d, want %+v", i, p.ID, p.PDFPage, len(p.Lines), w)
		}
	}
	d := ann.Pages[2]
	if len(d.Highlights) != 1 || len(d.Highlights[0].R) != 8 {
		t.Errorf("highlights %+v", d.Highlights)
	}
	if got := len(d.Lines[1].P); got != 3*6 {
		t.Errorf("second line has %d values, want 18", got)
	}
}

func TestAnnotations_FormatVersion1(t *testing.T) {
	doc, rs := annDoc(map[string][]byte{
		"doc.content": []byte(`{"fileType":"pdf","pages":["p0","p1","p2"],"redirectionPageMap":[0,-1,1]}`),
	})
	ann, err := AnnotationsFromHashDoc(doc, rs)
	if err != nil {
		t.Fatal(err)
	}
	got := []int{}
	for _, p := range ann.Pages {
		got = append(got, p.PDFPage)
	}
	if len(got) != 3 || got[0] != 0 || got[1] != -1 || got[2] != 1 {
		t.Errorf("pdf pages %v, want [0 -1 1]", got)
	}
	if ann.HasPDF {
		t.Error("hasPdf without a pdf file")
	}
}

func TestAnnotations_BadPageDoesNotHideOthers(t *testing.T) {
	doc, rs := annDoc(map[string][]byte{
		"doc.content": []byte(`{"fileType":"notebook","cPages":{"pages":[{"id":"a","idx":{"value":"a"}},{"id":"b","idx":{"value":"b"}}]}}`),
		"doc/a.rm":    []byte("reMarkable .lines file, version=5          garbage"),
		"doc/b.rm":    synthetic(t),
	})
	ann, err := AnnotationsFromHashDoc(doc, rs)
	if err != nil {
		t.Fatal(err)
	}
	if len(ann.Pages) != 2 || len(ann.Pages[0].Lines) != 0 || len(ann.Pages[1].Lines) != 3 {
		t.Errorf("pages %+v", ann.Pages)
	}
}

func TestPDFFromHashDoc_PrefersPDFOverEPUB(t *testing.T) {
	doc, rs := annDoc(map[string][]byte{"doc.epub": []byte("PK epub"), "doc.pdf": []byte("%PDF rendition")})
	r, err := PDFFromHashDoc(doc, rs)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(r)
	if string(b) != "%PDF rendition" {
		t.Errorf("got %q", b)
	}
	doc, rs = annDoc(map[string][]byte{"doc.epub": []byte("PK epub")})
	if _, err := PDFFromHashDoc(doc, rs); !errors.Is(err, ErrNoPayload) {
		t.Errorf("epub without rendition: err=%v, want ErrNoPayload", err)
	}
}

func TestArchiveFromHashDoc_EpubWithRenditionUsesPDF(t *testing.T) {
	for _, order := range [][]string{{"doc.epub", "doc.pdf"}, {"doc.pdf", "doc.epub"}} {
		rs := memStorage{"c": []byte(`{"fileType":"epub"}`), "doc.epub": []byte("PK epub"), "doc.pdf": []byte("%PDF rendition")}
		doc := &HashDoc{HashEntry: HashEntry{EntryName: "doc"}, Files: []*HashEntry{{Hash: "c", EntryName: "doc.content"}}}
		for _, n := range order {
			doc.Files = append(doc.Files, &HashEntry{Hash: n, EntryName: n})
		}
		a, err := ArchiveFromHashDoc(doc, rs)
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(a.PayloadReader)
		if string(b) != "%PDF rendition" {
			t.Errorf("order %v: payload %q", order, b)
		}
	}
}
