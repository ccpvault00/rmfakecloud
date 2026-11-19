package models

import (
	"bytes"
	"fmt"
	"io"
	"testing"
)

// memStorage serves blobs by hash; bytes.Reader satisfies the io.ReadSeekCloser
// that ArchiveFromHashDoc expects for the pdf payload.
type memStorage map[string][]byte

type seekNopCloser struct{ *bytes.Reader }

func (seekNopCloser) Close() error { return nil }

func (m memStorage) GetRootIndex() (string, int64, error) { return "", 0, nil }

func (m memStorage) GetReader(hash string) (io.ReadCloser, error) {
	b, ok := m[hash]
	if !ok {
		return nil, fmt.Errorf("blob %s not found", hash)
	}
	return seekNopCloser{bytes.NewReader(b)}, nil
}

func pdfDoc(content string) (*HashDoc, memStorage) {
	const uuid = "8d9d3592-5320-4f76-afa9-8e542598d6f9"
	rs := memStorage{
		"content": []byte(content),
		"pdf":     []byte("%PDF-1.4 test"),
	}
	doc := &HashDoc{
		HashEntry: HashEntry{EntryName: uuid},
		Files: []*HashEntry{
			{Hash: "content", EntryName: uuid + ".content"},
			{Hash: "pdf", EntryName: uuid + ".pdf"},
		},
	}
	return doc, rs
}

func TestArchiveFromHashDoc_PageTagsAsObjects(t *testing.T) {
	// firmware 3.x writes pageTags as objects (formatVersion 2 .content)
	doc, rs := pdfDoc(`{"fileType":"pdf","formatVersion":2,"pageCount":3,
		"pageTags":[{"name":"daily plan","pageId":"0c4d7a6e-588a-4bd7-af81-6d750d301580","timestamp":1769609910755}]}`)

	a, err := ArchiveFromHashDoc(doc, rs)
	if err != nil {
		t.Fatalf("ArchiveFromHashDoc: %v", err)
	}
	if a.Content.FileType != "pdf" || a.Content.PageCount != 3 {
		t.Errorf("content not decoded: fileType=%q pageCount=%d", a.Content.FileType, a.Content.PageCount)
	}
	if len(a.Content.Tags) != 1 || a.Content.Tags[0] != "daily plan" {
		t.Errorf("tags = %v, want [daily plan]", a.Content.Tags)
	}
	if a.PayloadReader == nil {
		t.Error("pdf payload missing")
	}
}

func TestArchiveFromHashDoc_PageTagsAsStrings(t *testing.T) {
	doc, rs := pdfDoc(`{"fileType":"pdf","pageCount":1,"pageTags":["a","b"]}`)

	a, err := ArchiveFromHashDoc(doc, rs)
	if err != nil {
		t.Fatalf("ArchiveFromHashDoc: %v", err)
	}
	if len(a.Content.Tags) != 2 || a.Content.Tags[0] != "a" || a.Content.Tags[1] != "b" {
		t.Errorf("tags = %v, want [a b]", a.Content.Tags)
	}
}

func TestArchiveFromHashDoc_InvalidContent(t *testing.T) {
	doc, rs := pdfDoc(`{"pageTags":[1]}`)

	if _, err := ArchiveFromHashDoc(doc, rs); err == nil {
		t.Error("expected an error for numeric pageTags")
	}
}
