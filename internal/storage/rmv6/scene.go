package rmv6

import (
	"errors"
	"fmt"
	"sort"
)

// block types
const (
	blockSceneTree = 0x01
	blockTreeNode  = 0x02
	blockGlyphItem = 0x03
	blockGroupItem = 0x04
	blockLineItem  = 0x05
	blockRootText  = 0x07
)

// item types inside a scene item's value sub-block
const (
	itemGlyph = 0x01
	itemGroup = 0x02
	itemLine  = 0x03
)

// Point is one sample of a stroke, in v2 units (as written by 3.x software).
type Point struct {
	X, Y      float32
	Speed     uint16
	Width     uint16
	Direction uint8
	Pressure  uint8
}

// Line is a visible stroke. Coordinates are page units: x is relative to the page
// centre, y to the top; group anchor offsets are already applied.
type Line struct {
	Tool           uint32
	Color          uint32
	ThicknessScale float64
	Points         []Point
}

// Rect is a highlight rectangle in page units.
type Rect struct {
	X, Y, W, H float64
}

// Highlight is text highlighted on the PDF/EPUB page underneath.
type Highlight struct {
	Color uint32
	Text  string
	Rects []Rect
}

// Scene is everything the viewer draws for one page.
type Scene struct {
	Lines      []Line
	Highlights []Highlight
	// HasText is set when the page contains typed text, which is not decoded.
	HasText bool
	// Skipped counts blocks that could not be decoded (newer format or corrupt).
	Skipped int
}

var rootID = crdtID{0, 1}

type node struct {
	visible    bool
	hasAnchor  bool
	anchorID   crdtID
	anchorX    float32
	childItems []*item
}

type item struct {
	id, left, right crdtID
	kind            uint8 // 0 = deleted (no value)
	group           crdtID
	line            *Line
	glyph           *Highlight
}

// Parse decodes a v6 scene file.
func Parse(data []byte) (*Scene, error) {
	if len(data) < len(Header) || string(data[:len(Header)]) != Header {
		return nil, errors.New("rmv6: not a v6 .rm file")
	}
	r := &reader{b: data, pos: len(Header)}
	nodes := map[crdtID]*node{rootID: {visible: true}}
	getNode := func(id crdtID) *node {
		n, ok := nodes[id]
		if !ok {
			n = &node{visible: true}
			nodes[id] = n
		}
		return n
	}
	scene := &Scene{}

	for r.remaining() > 0 {
		length, err := r.u32()
		if err != nil {
			return nil, err
		}
		hdr, err := r.bytes(4) // unknown, min_version, current_version, block_type
		if err != nil {
			return nil, err
		}
		payload, err := r.bytes(int(length))
		if err != nil {
			return nil, fmt.Errorf("rmv6: block of %d bytes runs past end of file", length)
		}
		version, blockType := hdr[2], hdr[3]
		b := &reader{b: payload}

		switch blockType {
		case blockSceneTree:
			id, err := b.id(1)
			if err != nil {
				scene.Skipped++
				continue
			}
			getNode(id)
		case blockTreeNode:
			if err := readTreeNode(b, getNode); err != nil {
				scene.Skipped++
			}
		case blockGroupItem, blockLineItem, blockGlyphItem:
			parent, it, err := readItem(b, blockType, version)
			if err != nil {
				scene.Skipped++
				continue
			}
			p := getNode(parent)
			p.childItems = append(p.childItems, it)
		case blockRootText:
			scene.HasText = true
		}
	}

	walk(nodes, rootID, 0, map[crdtID]bool{}, scene)
	return scene, nil
}

func readTreeNode(b *reader, getNode func(crdtID) *node) error {
	id, err := b.id(1)
	if err != nil {
		return err
	}
	if _, err = b.subblock(2); err != nil { // label
		return err
	}
	visible, err := b.lwwBool(3)
	if err != nil {
		return err
	}
	n := getNode(id)
	n.visible = visible
	if b.remaining() > 0 && b.peekTag(7, tagLength4) {
		if n.anchorID, err = b.lwwID(7); err != nil {
			return err
		}
		if _, err = b.subblock(8); err != nil { // anchor type
			return err
		}
		if _, err = b.subblock(9); err != nil { // anchor threshold
			return err
		}
		if n.anchorX, err = b.lwwFloat(10); err != nil {
			return err
		}
		n.hasAnchor = true
	}
	return nil
}

func readItem(b *reader, blockType, version uint8) (crdtID, *item, error) {
	parent, err := b.id(1)
	if err != nil {
		return parent, nil, err
	}
	it := &item{}
	if it.id, err = b.id(2); err != nil {
		return parent, nil, err
	}
	if it.left, err = b.id(3); err != nil {
		return parent, nil, err
	}
	if it.right, err = b.id(4); err != nil {
		return parent, nil, err
	}
	if _, err = b.int32(5); err != nil { // deleted length
		return parent, nil, err
	}
	if !b.peekTag(6, tagLength4) {
		return parent, it, nil // deleted item: keeps its place in the order, draws nothing
	}
	v, err := b.subblock(6)
	if err != nil {
		return parent, nil, err
	}
	kind, err := v.u8()
	if err != nil {
		return parent, nil, err
	}
	switch {
	case blockType == blockGroupItem && kind == itemGroup:
		it.group, err = v.id(2)
	case blockType == blockLineItem && kind == itemLine:
		it.line, err = readLine(v, version)
	case blockType == blockGlyphItem && kind == itemGlyph:
		it.glyph, err = readGlyph(v)
	default:
		err = fmt.Errorf("rmv6: item type %d in block type %d", kind, blockType)
	}
	if err != nil {
		return parent, nil, err
	}
	it.kind = kind
	return parent, it, nil
}

func readLine(v *reader, version uint8) (*Line, error) {
	var l Line
	var err error
	if l.Tool, err = v.int32(1); err != nil {
		return nil, err
	}
	if l.Color, err = v.int32(2); err != nil {
		return nil, err
	}
	if l.ThicknessScale, err = v.double(3); err != nil {
		return nil, err
	}
	if _, err = v.float(4); err != nil { // starting length
		return nil, err
	}
	pts, err := v.subblock(5)
	if err != nil {
		return nil, err
	}
	size := 14
	if version == 1 {
		size = 24
	}
	if len(pts.b)%size != 0 {
		return nil, fmt.Errorf("rmv6: point data %d bytes is not a multiple of %d", len(pts.b), size)
	}
	l.Points = make([]Point, 0, len(pts.b)/size)
	for pts.remaining() > 0 {
		p, err := readPoint(pts, version)
		if err != nil {
			return nil, err
		}
		l.Points = append(l.Points, p)
	}
	return &l, nil
}

func readPoint(r *reader, version uint8) (Point, error) {
	var p Point
	var err error
	if p.X, err = r.f32(); err != nil {
		return p, err
	}
	if p.Y, err = r.f32(); err != nil {
		return p, err
	}
	if version == 1 {
		// v1 stores floats; convert to v2 units as rmscene does
		var speed, dir, width, pressure float32
		for _, f := range []*float32{&speed, &dir, &width, &pressure} {
			if *f, err = r.f32(); err != nil {
				return p, err
			}
		}
		p.Speed = clampU16(speed * 4)
		p.Direction = clampU8(255 * dir / (2 * 3.141592653589793))
		p.Width = clampU16(width*4 + 0.5)
		p.Pressure = clampU8(pressure * 255)
		return p, nil
	}
	if p.Speed, err = r.u16(); err != nil {
		return p, err
	}
	if p.Width, err = r.u16(); err != nil {
		return p, err
	}
	if p.Direction, err = r.u8(); err != nil {
		return p, err
	}
	p.Pressure, err = r.u8()
	return p, err
}

func clampU16(f float32) uint16 {
	if f < 0 {
		return 0
	}
	if f > 65535 {
		return 65535
	}
	return uint16(f)
}

func clampU8(f float32) uint8 {
	if f < 0 {
		return 0
	}
	if f > 255 {
		return 255
	}
	return uint8(f)
}

func readGlyph(v *reader) (*Highlight, error) {
	g := &Highlight{}
	var err error
	if v.peekTag(2, tagByte4) {
		if _, err = v.int32(2); err != nil { // start
			return nil, err
		}
	}
	if v.peekTag(3, tagByte4) {
		if _, err = v.int32(3); err != nil { // length
			return nil, err
		}
	}
	if g.Color, err = v.int32(4); err != nil {
		return nil, err
	}
	if g.Text, err = v.str(5); err != nil {
		return nil, err
	}
	rs, err := v.subblock(6)
	if err != nil {
		return nil, err
	}
	n, err := rs.varuint()
	if err != nil {
		return nil, err
	}
	if n > uint64(rs.remaining()/32) {
		return nil, fmt.Errorf("rmv6: %d highlight rectangles exceed the block", n)
	}
	for i := uint64(0); i < n; i++ {
		var q [4]float64
		for j := range q {
			if q[j], err = rs.f64(); err != nil {
				return nil, err
			}
		}
		g.Rects = append(g.Rects, Rect{q[0], q[1], q[2], q[3]})
	}
	return g, nil
}

// order sorts sibling items by their left/right links (rmscene's toposort_items):
// repeatedly emit every item whose left neighbour is already placed and which no
// unplaced item must precede, ties broken by id.
func order(items []*item) []*item {
	byID := make(map[crdtID]*item, len(items))
	for _, it := range items {
		byID[it.id] = it
	}
	const start, end = "start", "end"
	key := func(id crdtID) string { return fmt.Sprintf("%d:%d", id.p1, id.p2) }
	deps := map[string]map[string]bool{}
	add := func(k, dep string) {
		if deps[k] == nil {
			deps[k] = map[string]bool{}
		}
		if dep != "" {
			deps[k][dep] = true
		}
	}
	side := func(id crdtID, left bool) string {
		if _, ok := byID[id]; !ok || id == (crdtID{}) {
			if left {
				return start
			}
			return end
		}
		return key(id)
	}
	keyItem := map[string]*item{}
	for _, it := range byID {
		k := key(it.id)
		keyItem[k] = it
		add(k, side(it.left, true))
		add(side(it.right, false), k)
	}
	for _, d := range deps {
		for dep := range d {
			add(dep, "")
		}
	}
	var out []*item
	for len(deps) > 0 {
		var ready []string
		for k, d := range deps {
			if len(d) == 0 {
				ready = append(ready, k)
			}
		}
		if len(ready) == 0 {
			break // cycle: append the rest in id order below
		}
		var readyItems []*item
		for _, k := range ready {
			if it, ok := keyItem[k]; ok {
				readyItems = append(readyItems, it)
			}
			delete(deps, k)
		}
		sort.Slice(readyItems, func(i, j int) bool { return readyItems[i].id.less(readyItems[j].id) })
		out = append(out, readyItems...)
		for _, d := range deps {
			for _, k := range ready {
				delete(d, k)
			}
		}
		if len(deps) == 1 {
			if _, ok := deps[end]; ok {
				break
			}
		}
	}
	if len(out) < len(byID) { // cyclic links in a corrupt file: keep what is left in id order
		placed := map[crdtID]bool{}
		for _, it := range out {
			placed[it.id] = true
		}
		var rest []*item
		for _, it := range byID {
			if !placed[it.id] {
				rest = append(rest, it)
			}
		}
		sort.Slice(rest, func(i, j int) bool { return rest[i].id.less(rest[j].id) })
		out = append(out, rest...)
	}
	return out
}

func walk(nodes map[crdtID]*node, id crdtID, dx float32, seen map[crdtID]bool, scene *Scene) {
	n, ok := nodes[id]
	if !ok || seen[id] || !n.visible {
		return
	}
	seen[id] = true
	if n.hasAnchor {
		// anchors to typed text need the text layout; only the x origin is applied
		dx += n.anchorX
	}
	for _, it := range order(n.childItems) {
		switch it.kind {
		case itemGroup:
			walk(nodes, it.group, dx, seen, scene)
		case itemLine:
			l := *it.line
			if dx != 0 {
				pts := make([]Point, len(l.Points))
				for i, p := range l.Points {
					p.X += dx
					pts[i] = p
				}
				l.Points = pts
			}
			scene.Lines = append(scene.Lines, l)
		case itemGlyph:
			h := *it.glyph
			if dx != 0 {
				rects := make([]Rect, len(h.Rects))
				for i, q := range h.Rects {
					q.X += float64(dx)
					rects[i] = q
				}
				h.Rects = rects
			}
			scene.Highlights = append(scene.Highlights, h)
		}
	}
}
