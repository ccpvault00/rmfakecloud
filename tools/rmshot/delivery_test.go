package main

import (
	"image"
	"image/color"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func tinyImage() image.Image {
	img := image.NewRGBA(image.Rect(0, 0, 4, 3))
	img.Set(1, 1, color.RGBA{255, 0, 0, 255})
	return img
}

func setup(t *testing.T, allow string) (*Outbox, string) {
	t.Helper()
	inbox := t.TempDir()
	now := time.Date(2026, 11, 28, 21, 30, 0, 0, time.Local)
	rv := &Receiver{Dir: inbox, Allow: map[string]bool{allow: true}, Now: func() time.Time { return now }}
	srv := httptest.NewServer(rv)
	t.Cleanup(srv.Close)
	return &Outbox{Dir: filepath.Join(t.TempDir(), "outbox"), Dest: srv.URL + "/shot", Client: srv.Client()}, inbox
}

func entries(t *testing.T, dir string) []string {
	t.Helper()
	ents, _ := os.ReadDir(dir)
	var names []string
	for _, e := range ents {
		names = append(names, e.Name())
	}
	return names
}

func TestShotIsDeliveredNamedAfterCaptureTime(t *testing.T) {
	ob, inbox := setup(t, "127.0.0.1")
	taken := time.Date(2026, 11, 28, 21, 5, 9, 0, time.Local)
	if _, err := ob.Save(tinyImage(), taken); err != nil {
		t.Fatal(err)
	}
	if n, err := ob.Flush(); n != 1 || err != nil {
		t.Fatalf("flush = %d, %v", n, err)
	}
	got := entries(t, inbox)
	if len(got) != 1 || got[0] != "rm_shot_20261128-210509.png" {
		t.Fatalf("inbox = %v", got)
	}
	st, _ := os.Stat(filepath.Join(inbox, got[0]))
	if !st.ModTime().Equal(taken) {
		t.Fatalf("mtime %v, want the capture time (the /img skill orders by mtime)", st.ModTime())
	}
	if p, _ := ob.Pending(); len(p) != 0 {
		t.Fatalf("outbox not emptied: %v", p)
	}
}

func TestRetryDoesNotDuplicateButSameSecondShotsBothKept(t *testing.T) {
	ob, inbox := setup(t, "127.0.0.1")
	taken := time.Date(2026, 11, 28, 21, 5, 9, 0, time.Local)
	name, _ := ob.Save(tinyImage(), taken)
	data, _ := os.ReadFile(name)
	if _, err := ob.Flush(); err != nil {
		t.Fatal(err)
	}
	// the PC stored it but the answer was lost: the tablet sends the same file again
	_ = os.WriteFile(name, data, 0o644)
	if _, err := ob.Flush(); err != nil {
		t.Fatal(err)
	}
	// a different screen in the same second
	other := image.NewRGBA(image.Rect(0, 0, 2, 2))
	if _, err := ob.Save(other, taken.Add(300*time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if _, err := ob.Flush(); err != nil {
		t.Fatal(err)
	}
	got := strings.Join(entries(t, inbox), " ")
	if got != "rm_shot_20261128-210509.png rm_shot_20261128-210509_2.png" {
		t.Fatalf("inbox = %s", got)
	}
}

func TestRefusedShotStaysInTheOutbox(t *testing.T) {
	ob, inbox := setup(t, "100.102.185.106") // the test client is 127.0.0.1
	if _, err := ob.Save(tinyImage(), time.Now()); err != nil {
		t.Fatal(err)
	}
	n, err := ob.Flush()
	if n != 0 || err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatalf("flush = %d, %v; want a 403", n, err)
	}
	if p, _ := ob.Pending(); len(p) != 1 {
		t.Fatalf("outbox = %v, want the shot kept for a retry", p)
	}
	if got := entries(t, inbox); len(got) != 0 {
		t.Fatalf("inbox = %v", got)
	}
}

func TestReceiverRejectsNonPNGAndBadTimes(t *testing.T) {
	inbox := t.TempDir()
	now := time.Date(2026, 11, 28, 21, 30, 0, 0, time.Local)
	rv := &Receiver{Dir: inbox, Allow: map[string]bool{"192.0.2.1": true}, Now: func() time.Time { return now }}
	req := httptest.NewRequest(http.MethodPost, "/shot", strings.NewReader("hello"))
	w := httptest.NewRecorder()
	rv.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("non-PNG answered %d", w.Code)
	}
	for _, h := range []string{"", "junk", strconv.FormatInt(now.AddDate(5, 0, 0).UnixNano(), 10)} {
		if got := rv.taken(h); !got.Equal(now) {
			t.Fatalf("taken(%q) = %v, want now", h, got)
		}
	}
}
