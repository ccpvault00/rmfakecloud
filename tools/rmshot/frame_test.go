package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
)

// fakeMem is a sparse address space: one mapping of data starting at base.
type fakeMem struct {
	base uint64
	data []byte
}

func (m fakeMem) ReadAt(p []byte, off int64) (int, error) {
	o := uint64(off)
	if o < m.base || o+uint64(len(p)) > m.base+uint64(len(m.data)) {
		return 0, io.EOF
	}
	return copy(p, m.data[o-m.base:]), nil
}

const base = 0xffff7cbb3000

// mapping builds a region holding a frame at page `page`, every visible pixel (B,G,R)=(x%256, y%256, 7).
func mapping(page int, header uint64) fakeMem {
	data := make([]byte, (page+1)*pageSize+frameBytes+pageSize)
	h := page * pageSize
	binary.LittleEndian.PutUint64(data[h+8:], header)
	px := data[h+16:]
	for y := 0; y < screenH; y++ {
		for x := 0; x < stride; x++ {
			i := (y*stride + x) * 4
			px[i], px[i+1], px[i+2], px[i+3] = byte(x), byte(y), 7, 0xff
		}
	}
	return fakeMem{base, data}
}

func mapsFor(m fakeMem) string {
	end := m.base + uint64(len(m.data))
	return strings.Join([]string{
		"aaaac4b60000-aaaac62fd000 r-xp 00000000 b3:02 981        /usr/bin/xochitl",
		fmt.Sprintf("%x-%x rw-p 00000000 00:00 0", base-0x1000000, base-0x100000), // too small: skipped
		fmt.Sprintf("%x-%x rw-p 00000000 00:00 0", m.base, end),
		"ffff86660000-ffff86c77000 r-xp 00000000 b3:02 1352       /usr/lib/libicudata.so.78.3",
	}, "\n")
}

func TestGrabFindsTheFrameAndConvertsBGRA(t *testing.T) {
	m := mapping(1722, chunkHeader) // 0x6ba000, where it was on the real tablet
	img, err := grabScreen(strings.NewReader(mapsFor(m)), m)
	if err != nil {
		t.Fatal(err)
	}
	if b := img.Bounds(); b.Dx() != screenW || b.Dy() != screenH {
		t.Fatalf("size %v, want %dx%d (padding columns cut)", b, screenW, screenH)
	}
	for _, p := range [][2]int{{0, 0}, {1619, 2159}, {300, 1000}} {
		c := img.RGBAAt(p[0], p[1])
		if c.R != 7 || c.G != byte(p[1]) || c.B != byte(p[0]) || c.A != 0xff {
			t.Fatalf("pixel %v = %v, want R=7 G=%d B=%d", p, c, byte(p[1]), byte(p[0]))
		}
	}
}

func TestGrabWithoutTheHeaderFails(t *testing.T) {
	m := mapping(3, chunkHeader+0x1000)
	if _, err := grabScreen(strings.NewReader(mapsFor(m)), m); !errors.Is(err, ErrNoFrame) {
		t.Fatalf("err = %v, want ErrNoFrame", err)
	}
}

func TestGrabRefusesATransparentBuffer(t *testing.T) {
	m := mapping(3, chunkHeader)
	m.data[3*pageSize+16+3] = 0 // alpha of pixel 0,0
	if _, err := grabScreen(strings.NewReader(mapsFor(m)), m); err == nil || !strings.Contains(err.Error(), "opaque") {
		t.Fatalf("err = %v, want an opaque-image error", err)
	}
}

// RMSHOT_MAPPING=<dump of one xochitl mapping>@<hex start address> checks a real dump.
func TestRealDump(t *testing.T) {
	spec := os.Getenv("RMSHOT_MAPPING")
	if spec == "" {
		t.Skip("set RMSHOT_MAPPING=file@hexaddr to test a real memory dump")
	}
	file, addr, _ := strings.Cut(spec, "@")
	data, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	var start uint64
	fmt.Sscanf(addr, "%x", &start)
	m := fakeMem{start, data}
	img, err := grabScreen(strings.NewReader(mapsFor(m)), m)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("pixel (400,350) = %v", img.RGBAAt(400, 350))
}
