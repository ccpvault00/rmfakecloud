package main

import (
	"encoding/binary"
	"os"
	"testing"
	"time"
)

var t0 = time.Date(2026, 11, 28, 21, 0, 0, 0, time.UTC)

func at(ms int) time.Time { return t0.Add(time.Duration(ms) * time.Millisecond) }

// down/up emit the protocol-B events for one finger in one slot.
func down(d *Detector, ms int, slot int32) bool {
	d.Event(at(ms), evAbs, absMTSlot, slot)
	return d.Event(at(ms), evAbs, absMTTrackingID, 100+slot)
}

func up(d *Detector, ms int, slot int32) bool {
	d.Event(at(ms), evAbs, absMTSlot, slot)
	return d.Event(at(ms), evAbs, absMTTrackingID, -1)
}

func TestHoldFiresOnceAfterHoldTime(t *testing.T) {
	d := NewDetector(4, time.Second)
	for s := int32(0); s < 4; s++ {
		if down(d, 0, s) {
			t.Fatal("fired on touch-down")
		}
	}
	if at, ok := d.Deadline(); !ok || !at.Equal(t0.Add(time.Second)) {
		t.Fatalf("deadline = %v %v, want t0+1s", at, ok)
	}
	if d.Tick(at(999)) {
		t.Fatal("fired before the hold time")
	}
	if !d.Tick(at(1000)) {
		t.Fatal("did not fire at the hold time")
	}
	if d.Tick(at(3000)) {
		t.Fatal("fired twice for one hold")
	}
	// lifting one finger and putting it back is still the same touch
	up(d, 3100, 3)
	down(d, 3200, 3)
	if d.Tick(at(9000)) {
		t.Fatal("fired again without lifting every finger")
	}
	for s := int32(0); s < 4; s++ {
		up(d, 9100, s)
	}
	for s := int32(0); s < 4; s++ {
		down(d, 9500, s)
	}
	if !d.Tick(at(10500)) {
		t.Fatal("second hold after a full lift did not fire")
	}
}

func TestHoldClockStartsWithTheLastFinger(t *testing.T) {
	d := NewDetector(4, time.Second)
	for s := int32(0); s < 3; s++ {
		down(d, 0, s)
	}
	if d.Tick(at(5000)) {
		t.Fatal("three fingers fired")
	}
	down(d, 5000, 3)
	if d.Tick(at(5900)) || !d.Tick(at(6000)) {
		t.Fatal("hold time must count from the fourth finger")
	}
}

func TestSynDroppedForgetsFingers(t *testing.T) {
	d := NewDetector(4, time.Second)
	for s := int32(0); s < 4; s++ {
		down(d, 0, s)
	}
	d.Event(at(500), evSyn, synDropped, 0)
	if d.Tick(at(2000)) {
		t.Fatal("fired on fingers the kernel may have lost")
	}
}

// The fixture is the real Paper Pro touchscreen (OS 3.29) recorded while the user did:
// a 4-finger hold (4.4 s), a 3-finger hold (3.9 s), two quick 4-finger taps (0.15 s, 0.12 s)
// and later another 4-finger hold (2.6 s). Exactly the two holds must fire.
func TestRecordedPaperProGestures(t *testing.T) {
	raw, err := os.ReadFile("testdata/paperpro_gestures.bin")
	if err != nil {
		t.Fatal(err)
	}
	d := NewDetector(4, time.Second)
	var fires []time.Duration
	var first time.Time
	check := func(now time.Time) { // what the daemon's timer does between events
		if at, ok := d.Deadline(); ok && !now.Before(at) && d.Tick(at) {
			fires = append(fires, at.Sub(first))
		}
	}
	for i := 0; i+24 <= len(raw); i += 24 {
		b := raw[i : i+24]
		now := time.Unix(int64(binary.LittleEndian.Uint64(b)), int64(binary.LittleEndian.Uint64(b[8:]))*1000)
		if first.IsZero() {
			first = now
		}
		check(now)
		if d.Event(now, binary.LittleEndian.Uint16(b[16:]), binary.LittleEndian.Uint16(b[18:]), int32(binary.LittleEndian.Uint32(b[20:]))) {
			fires = append(fires, now.Sub(first))
		}
	}
	if len(fires) != 2 {
		t.Fatalf("fired %d times at %v, want 2 (the two 4-finger holds)", len(fires), fires)
	}
	if fires[0] > 1100*time.Millisecond || fires[1] < 98*time.Second {
		t.Fatalf("fired at %v, want ~1 s and ~99 s", fires)
	}
}
