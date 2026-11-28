package main

import (
	"bytes"
	"fmt"
	"image"
	"image/png"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Outbox keeps captured screenshots on the tablet until the PC has accepted them,
// so a shot taken while offline is delivered later instead of lost.
type Outbox struct {
	Dir    string
	Dest   string // receiver URL, e.g. http://100.x.y.z:18474/shot
	Client *http.Client
}

// Save encodes img as PNG into the outbox, named by the capture time (unix nanoseconds).
func (o *Outbox) Save(img image.Image, taken time.Time) (string, error) {
	if err := os.MkdirAll(o.Dir, 0o755); err != nil {
		return "", err
	}
	var buf bytes.Buffer
	enc := png.Encoder{CompressionLevel: png.BestSpeed}
	if err := enc.Encode(&buf, img); err != nil {
		return "", fmt.Errorf("encode png: %w", err)
	}
	name := filepath.Join(o.Dir, strconv.FormatInt(taken.UnixNano(), 10)+".png")
	tmp := name + ".tmp"
	if err := os.WriteFile(tmp, buf.Bytes(), 0o644); err != nil {
		return "", err
	}
	return name, os.Rename(tmp, name)
}

// Pending lists outbox files, oldest first.
func (o *Outbox) Pending() ([]string, error) {
	files, err := filepath.Glob(filepath.Join(o.Dir, "*.png"))
	sort.Strings(files) // same-length decimal nanosecond names sort by time
	return files, err
}

// Flush sends every pending file; a file is deleted only after the receiver accepted it.
// It returns the number sent and the first error (later files are kept for the next try).
func (o *Outbox) Flush() (int, error) {
	files, err := o.Pending()
	if err != nil {
		return 0, err
	}
	for i, f := range files {
		if err := o.send(f); err != nil {
			return i, fmt.Errorf("%s: %w", filepath.Base(f), err)
		}
		if err := os.Remove(f); err != nil {
			return i + 1, err
		}
	}
	return len(files), nil
}

func (o *Outbox) send(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(http.MethodPost, o.Dest, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "image/png")
	req.Header.Set(takenHeader, strings.TrimSuffix(filepath.Base(path), ".png"))
	resp, err := o.Client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 200))
		return fmt.Errorf("receiver answered %s: %s", resp.Status, strings.TrimSpace(string(msg)))
	}
	return nil
}
