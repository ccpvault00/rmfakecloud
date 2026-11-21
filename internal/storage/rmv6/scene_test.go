package rmv6

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// expected is the JSON written by testdata/oracle_dump.py (rmscene as the reference).
type expected struct {
	Lines []struct {
		Tool      uint32      `json:"tool"`
		Color     uint32      `json:"color"`
		Thickness float64     `json:"thickness"`
		Points    [][]float64 `json:"points"`
	} `json:"lines"`
	Highlights []struct {
		Color uint32      `json:"color"`
		Text  string      `json:"text"`
		Rects [][]float64 `json:"rects"`
	} `json:"highlights"`
	HasText bool `json:"hasText"`
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-2 }

func compare(t *testing.T, name string, got *Scene, want expected) {
	t.Helper()
	if got.HasText != want.HasText {
		t.Errorf("%s: hasText %v, want %v", name, got.HasText, want.HasText)
	}
	if len(got.Lines) != len(want.Lines) {
		t.Fatalf("%s: %d lines, want %d", name, len(got.Lines), len(want.Lines))
	}
	for i, w := range want.Lines {
		g := got.Lines[i]
		if g.Tool != w.Tool || g.Color != w.Color || !near(g.ThicknessScale, w.Thickness) || len(g.Points) != len(w.Points) {
			t.Fatalf("%s: line %d = tool %d color %d thickness %v %d points, want %d %d %v %d", name, i,
				g.Tool, g.Color, g.ThicknessScale, len(g.Points), w.Tool, w.Color, w.Thickness, len(w.Points))
		}
		for j, wp := range w.Points {
			p := g.Points[j]
			gp := []float64{float64(p.X), float64(p.Y), float64(p.Speed), float64(p.Width), float64(p.Direction), float64(p.Pressure)}
			for k := range wp {
				if !near(gp[k], wp[k]) {
					t.Fatalf("%s: line %d point %d = %v, want %v", name, i, j, gp, wp)
				}
			}
		}
	}
	if len(got.Highlights) != len(want.Highlights) {
		t.Fatalf("%s: %d highlights, want %d", name, len(got.Highlights), len(want.Highlights))
	}
	for i, w := range want.Highlights {
		g := got.Highlights[i]
		if g.Color != w.Color || g.Text != w.Text || len(g.Rects) != len(w.Rects) {
			t.Fatalf("%s: highlight %d = %d %q %d rects, want %d %q %d", name, i, g.Color, g.Text, len(g.Rects), w.Color, w.Text, len(w.Rects))
		}
		for j, wr := range w.Rects {
			r := g.Rects[j]
			if !near(r.X, wr[0]) || !near(r.Y, wr[1]) || !near(r.W, wr[2]) || !near(r.H, wr[3]) {
				t.Fatalf("%s: highlight %d rect %d = %v, want %v", name, i, j, r, wr)
			}
		}
	}
}

func load(t *testing.T, rmPath string) (*Scene, expected) {
	t.Helper()
	data, err := os.ReadFile(rmPath)
	if err != nil {
		t.Fatal(err)
	}
	got, err := Parse(data)
	if err != nil {
		t.Fatalf("%s: %v", rmPath, err)
	}
	var want expected
	js, err := os.ReadFile(strings.TrimSuffix(rmPath, ".rm") + ".json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(js, &want); err != nil {
		t.Fatal(err)
	}
	return got, want
}

// TestSynthetic covers ordering by left/right links, a deleted item, a hidden layer,
// an anchored layer (x offset) and a glyph highlight; see testdata/make_fixture.py.
func TestSynthetic(t *testing.T) {
	got, want := load(t, "testdata/synthetic.rm")
	compare(t, "synthetic", got, want)
	if got.Skipped != 0 {
		t.Errorf("skipped %d blocks", got.Skipped)
	}
}

// TestOracleDir compares against rmscene on real tablet files kept outside the repo:
// RMV6_ORACLE_DIR holds x.rm + x.json pairs written by testdata/oracle_dump.py.
func TestOracleDir(t *testing.T) {
	dir := os.Getenv("RMV6_ORACLE_DIR")
	if dir == "" {
		t.Skip("RMV6_ORACLE_DIR not set")
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.rm"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no .rm files in %s: %v", dir, err)
	}
	lines := 0
	for _, f := range files {
		got, want := load(t, f)
		compare(t, filepath.Base(f), got, want)
		lines += len(got.Lines)
	}
	t.Logf("%d files, %d lines match rmscene", len(files), lines)
}

func TestRejectsOtherFormats(t *testing.T) {
	if _, err := Parse([]byte("reMarkable .lines file, version=5          ")); err == nil {
		t.Error("v5 header accepted")
	}
	if _, err := Parse(nil); err == nil {
		t.Error("empty input accepted")
	}
}

// TestCorruptInputNeverPanics truncates and bit-flips the fixture: Parse may fail but
// must neither panic nor read out of bounds.
func TestCorruptInputNeverPanics(t *testing.T) {
	data, err := os.ReadFile("testdata/synthetic.rm")
	if err != nil {
		t.Fatal(err)
	}
	for n := 0; n < len(data); n++ {
		_, _ = Parse(data[:n])
	}
	buf := make([]byte, len(data))
	for i := len(Header); i < len(data); i++ {
		for _, x := range []byte{0x01, 0x80, 0xff} {
			copy(buf, data)
			buf[i] ^= x
			_, _ = Parse(buf)
		}
	}
}

// TestOrderLongChain: a real page is one long chain (each stroke left of the next);
// ordering must stay fast and keep the chain order even when ids run backwards.
func TestOrderLongChain(t *testing.T) {
	const n = 100000
	items := make([]*item, n)
	for i := range items {
		// position i gets id n-i, so id order is the reverse of the chain order
		items[i] = &item{id: crdtID{1, uint64(n - i)}}
	}
	for i := range items {
		if i > 0 {
			items[i].left = items[i-1].id
		}
		if i < n-1 {
			items[i].right = items[i+1].id
		}
	}
	shuffled := append([]*item(nil), items...)
	for i := range shuffled { // deterministic shuffle
		j := (i * 7919) % n
		shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
	}
	t0 := time.Now()
	got := order(shuffled)
	if d := time.Since(t0); d > 2*time.Second {
		t.Errorf("ordering %d items took %v", n, d)
	}
	if len(got) != n {
		t.Fatalf("got %d items", len(got))
	}
	for i := range got {
		if got[i] != items[i] {
			t.Fatalf("position %d: %s, want %s", i, fmt.Sprint(got[i].id), fmt.Sprint(items[i].id))
		}
	}
}

func TestOrderCycleKeepsAllItems(t *testing.T) {
	a := &item{id: crdtID{1, 1}}
	b := &item{id: crdtID{1, 2}}
	a.left, b.left = b.id, a.id // a after b and b after a
	if got := order([]*item{a, b}); len(got) != 2 || got[0] != a || got[1] != b {
		t.Errorf("cycle: %v", got)
	}
}
