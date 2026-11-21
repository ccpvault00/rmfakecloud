// Package rmv6 reads the strokes and PDF highlights of reMarkable v6 ".rm" scene files
// (tablet software 3.x).
//
// The format is a sequence of tagged blocks. The layout follows rmscene
// (https://github.com/ricklupton/rmscene, MIT), which in turn credits ddvk's reader.
// Only what the web viewer draws is decoded: lines, glyph highlights, groups (layers)
// and their visibility/anchor; typed text is detected but not decoded.
package rmv6

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

// Header is the magic prefix of every v6 file.
const Header = "reMarkable .lines file, version=6          "

// tag types: the low nibble of a tag varuint
const (
	tagByte1   = 0x1
	tagByte4   = 0x4
	tagByte8   = 0x8
	tagLength4 = 0xC
	tagID      = 0xF
)

var errShort = errors.New("rmv6: unexpected end of data")

// crdtID identifies a scene node or item; it orders like rmscene's CrdtId (part1, part2).
type crdtID struct {
	p1 uint8
	p2 uint64
}

func (a crdtID) less(b crdtID) bool {
	if a.p1 != b.p1 {
		return a.p1 < b.p1
	}
	return a.p2 < b.p2
}

// reader decodes primitives from one bounded slice (a block or sub-block), so a
// corrupt length can never read past the region it belongs to.
type reader struct {
	b   []byte
	pos int
}

func (r *reader) remaining() int { return len(r.b) - r.pos }

func (r *reader) bytes(n int) ([]byte, error) {
	if n < 0 || r.remaining() < n {
		return nil, errShort
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v, nil
}

func (r *reader) u8() (uint8, error) {
	b, err := r.bytes(1)
	if err != nil {
		return 0, err
	}
	return b[0], nil
}

func (r *reader) u16() (uint16, error) {
	b, err := r.bytes(2)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint16(b), nil
}

func (r *reader) u32() (uint32, error) {
	b, err := r.bytes(4)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(b), nil
}

func (r *reader) f32() (float32, error) {
	v, err := r.u32()
	return math.Float32frombits(v), err
}

func (r *reader) f64() (float64, error) {
	b, err := r.bytes(8)
	if err != nil {
		return 0, err
	}
	return math.Float64frombits(binary.LittleEndian.Uint64(b)), nil
}

func (r *reader) varuint() (uint64, error) {
	var result uint64
	for shift := uint(0); shift < 64; shift += 7 {
		c, err := r.u8()
		if err != nil {
			return 0, err
		}
		result |= uint64(c&0x7f) << shift
		if c&0x80 == 0 {
			return result, nil
		}
	}
	return 0, errors.New("rmv6: varuint overflow")
}

// peekTag reports whether the next tag is (index, typ) without consuming it.
func (r *reader) peekTag(index uint64, typ uint8) bool {
	save := r.pos
	defer func() { r.pos = save }()
	x, err := r.varuint()
	return err == nil && x>>4 == index && uint8(x&0xf) == typ
}

func (r *reader) tag(index uint64, typ uint8) error {
	at := r.pos
	x, err := r.varuint()
	if err != nil {
		return err
	}
	if x>>4 != index || uint8(x&0xf) != typ {
		r.pos = at
		return fmt.Errorf("rmv6: expected tag %d/0x%x, got %d/0x%x at %d", index, typ, x>>4, x&0xf, at)
	}
	return nil
}

func (r *reader) id(index uint64) (crdtID, error) {
	if err := r.tag(index, tagID); err != nil {
		return crdtID{}, err
	}
	p1, err := r.u8()
	if err != nil {
		return crdtID{}, err
	}
	p2, err := r.varuint()
	return crdtID{p1, p2}, err
}

func (r *reader) boolean(index uint64) (bool, error) {
	if err := r.tag(index, tagByte1); err != nil {
		return false, err
	}
	v, err := r.u8()
	return v != 0, err
}

func (r *reader) int32(index uint64) (uint32, error) {
	if err := r.tag(index, tagByte4); err != nil {
		return 0, err
	}
	return r.u32()
}

func (r *reader) float(index uint64) (float32, error) {
	if err := r.tag(index, tagByte4); err != nil {
		return 0, err
	}
	return r.f32()
}

func (r *reader) double(index uint64) (float64, error) {
	if err := r.tag(index, tagByte8); err != nil {
		return 0, err
	}
	return r.f64()
}

// subblock returns a reader bounded to the length-prefixed sub-block at index and
// advances past it, whatever the caller reads from it (newer firmware appends fields).
func (r *reader) subblock(index uint64) (*reader, error) {
	if err := r.tag(index, tagLength4); err != nil {
		return nil, err
	}
	n, err := r.u32()
	if err != nil {
		return nil, err
	}
	b, err := r.bytes(int(n))
	if err != nil {
		return nil, err
	}
	return &reader{b: b}, nil
}

func (r *reader) str(index uint64) (string, error) {
	s, err := r.subblock(index)
	if err != nil {
		return "", err
	}
	n, err := s.varuint()
	if err != nil {
		return "", err
	}
	if _, err = s.u8(); err != nil { // is_ascii flag
		return "", err
	}
	b, err := s.bytes(int(n))
	return string(b), err
}

// lww values are sub-blocks holding a timestamp (1) and the value (2).
func (r *reader) lwwBool(index uint64) (bool, error) {
	s, err := r.subblock(index)
	if err != nil {
		return false, err
	}
	if _, err = s.id(1); err != nil {
		return false, err
	}
	return s.boolean(2)
}

func (r *reader) lwwID(index uint64) (crdtID, error) {
	s, err := r.subblock(index)
	if err != nil {
		return crdtID{}, err
	}
	if _, err = s.id(1); err != nil {
		return crdtID{}, err
	}
	return s.id(2)
}

func (r *reader) lwwFloat(index uint64) (float32, error) {
	s, err := r.subblock(index)
	if err != nil {
		return 0, err
	}
	if _, err = s.id(1); err != nil {
		return 0, err
	}
	return s.float(2)
}
