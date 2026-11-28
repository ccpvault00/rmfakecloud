package main

import "time"

// Linux input event codes used by the multitouch protocol B (linux/input-event-codes.h).
const (
	evSyn           = 0x00
	evAbs           = 0x03
	synDropped      = 0x03
	absMTSlot       = 0x2f
	absMTTrackingID = 0x39
)

// Detector recognises "N fingers held for at least Hold": it fires once per touch,
// and only again after every finger has been lifted.
//
// The touchscreen reports only changes, so a still hand produces no events; the caller
// must also call Tick when Deadline passes.
type Detector struct {
	Fingers int
	Hold    time.Duration

	slot   int32
	active map[int32]bool
	since  time.Time // when Fingers were first down together; zero when not
	fired  bool
}

// NewDetector returns a detector for fingers held for hold.
func NewDetector(fingers int, hold time.Duration) *Detector {
	return &Detector{Fingers: fingers, Hold: hold, active: map[int32]bool{}}
}

// Event feeds one input event received at now; it reports whether the gesture completed.
func (d *Detector) Event(now time.Time, typ, code uint16, value int32) bool {
	switch {
	case typ == evSyn && code == synDropped: // the kernel lost events: start over
		d.active = map[int32]bool{}
	case typ == evAbs && code == absMTSlot:
		d.slot = value
	case typ == evAbs && code == absMTTrackingID:
		if value < 0 {
			delete(d.active, d.slot)
		} else {
			d.active[d.slot] = true
		}
	default:
		return d.Tick(now)
	}
	switch n := len(d.active); {
	case n == 0:
		d.since, d.fired = time.Time{}, false
	case n < d.Fingers:
		d.since = time.Time{}
	case d.since.IsZero():
		d.since = now
	}
	return d.Tick(now)
}

// Deadline is when Tick should next be called, if a gesture is in progress.
func (d *Detector) Deadline() (time.Time, bool) {
	if d.since.IsZero() || d.fired {
		return time.Time{}, false
	}
	return d.since.Add(d.Hold), true
}

// Tick reports whether the hold has lasted long enough at now (once per touch).
func (d *Detector) Tick(now time.Time) bool {
	if at, ok := d.Deadline(); ok && !now.Before(at) {
		d.fired = true
		return true
	}
	return false
}
