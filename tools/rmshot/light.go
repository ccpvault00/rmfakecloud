package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Light signals through the front light, the one output the Paper Pro has besides the screen
// (no LED, no vibration, and xochitl has no API for messages). A blink is full brightness then
// fully off, whatever the user's level; afterwards the user's level is restored.
type Light struct {
	Dir   string        // e.g. /sys/class/backlight/rm_frontlight
	Pulse time.Duration // length of one blink

	mu      sync.Mutex
	written []int // every level set, for tests
}

func (l *Light) read(name string) (int, error) {
	b, err := os.ReadFile(filepath.Join(l.Dir, name))
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(strings.TrimSpace(string(b)))
}

func (l *Light) set(v int) error {
	l.written = append(l.written, v)
	return os.WriteFile(filepath.Join(l.Dir, "brightness"), []byte(strconv.Itoa(v)), 0o644)
}

// Blink blinks n times and leaves the brightness as it found it.
func (l *Light) Blink(n int) error {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.written = l.written[:0]
	level, err := l.read("brightness")
	if err != nil {
		return fmt.Errorf("front light: %w", err)
	}
	max, err := l.read("max_brightness")
	if err != nil {
		return fmt.Errorf("front light: %w", err)
	}
	defer l.set(level) //nolint:errcheck // best effort: restore even if a write failed
	for i := 0; i < n; i++ {
		for _, v := range []int{max, 0} {
			if err := l.set(v); err != nil {
				return fmt.Errorf("front light: %w", err)
			}
			time.Sleep(l.Pulse)
		}
	}
	return nil
}
