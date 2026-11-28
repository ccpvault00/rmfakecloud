package main

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"io"
	"strconv"
	"strings"
)

// The Paper Pro's xochitl (OS 3.29) keeps the composed screen, toolbar included, as a
// 1632x2160 BGRA image (1620 visible columns + 12 padding) in its own malloc chunk. Found by
// inspecting /proc/<pid>/mem; the chunk is page aligned and its glibc header holds a fixed
// size word, which is how we find it again after the address moves.
const (
	stride      = 1632
	screenW     = 1620
	screenH     = 2160
	frameBytes  = stride * screenH * 4
	chunkHeader = 0xd72ff2 // glibc chunk size word: frameBytes + overhead, IS_MMAPPED flag
	pageSize    = 4096
)

// ErrNoFrame means no mapping of xochitl held the screen buffer (other OS version or orientation?).
var ErrNoFrame = errors.New("screen buffer not found in xochitl's memory")

type region struct{ start, end uint64 }

// candidateRegions lists the anonymous read-write mappings big enough to hold a frame.
func candidateRegions(maps io.Reader) ([]region, error) {
	var out []region
	sc := bufio.NewScanner(maps)
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) != 5 || !strings.HasPrefix(f[1], "rw") { // a 6th field is a file or [heap]/[stack]
			continue
		}
		lo, hi, ok := strings.Cut(f[0], "-")
		if !ok {
			continue
		}
		a, err1 := strconv.ParseUint(lo, 16, 64)
		b, err2 := strconv.ParseUint(hi, 16, 64)
		if err1 != nil || err2 != nil || b-a < frameBytes+16 {
			continue
		}
		out = append(out, region{a, b})
	}
	return out, sc.Err()
}

// findFrame returns the address of the first frame byte (just after the chunk header).
func findFrame(mem io.ReaderAt, regions []region) (uint64, error) {
	var word [8]byte
	for _, r := range regions {
		for p := r.start; p+16+frameBytes <= r.end; p += pageSize {
			if _, err := mem.ReadAt(word[:], int64(p+8)); err != nil {
				break // unreadable mapping: try the next one
			}
			if binary.LittleEndian.Uint64(word[:]) == chunkHeader {
				return p + 16, nil
			}
		}
	}
	return 0, ErrNoFrame
}

// readFrame copies the visible screen out of the buffer at addr as an opaque RGBA image.
func readFrame(mem io.ReaderAt, addr uint64) (*image.RGBA, error) {
	buf := make([]byte, frameBytes)
	if _, err := mem.ReadAt(buf, int64(addr)); err != nil {
		return nil, fmt.Errorf("read screen buffer: %w", err)
	}
	img := image.NewRGBA(image.Rect(0, 0, screenW, screenH))
	for y := 0; y < screenH; y++ {
		src := buf[y*stride*4 : (y*stride+screenW)*4]
		dst := img.Pix[y*img.Stride : y*img.Stride+screenW*4]
		for x := 0; x < len(src); x += 4 {
			if src[x+3] != 0xff {
				return nil, fmt.Errorf("screen buffer at %#x is not an opaque BGRA image (pixel %d,%d)", addr, x/4, y)
			}
			dst[x], dst[x+1], dst[x+2], dst[x+3] = src[x+2], src[x+1], src[x], 0xff
		}
	}
	return img, nil
}

// grabScreen captures xochitl's current screen; maps and mem are its /proc files.
func grabScreen(maps io.Reader, mem io.ReaderAt) (*image.RGBA, error) {
	regions, err := candidateRegions(maps)
	if err != nil {
		return nil, fmt.Errorf("read maps: %w", err)
	}
	addr, err := findFrame(mem, regions)
	if err != nil {
		return nil, err
	}
	return readFrame(mem, addr)
}
