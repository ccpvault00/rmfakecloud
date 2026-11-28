package main

import (
	"bytes"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

// takenHeader carries the capture time (unix nanoseconds) so a shot delivered late
// is still named after when it was taken.
const takenHeader = "X-Rmshot-Taken"

const maxShotBytes = 32 << 20

var pngMagic = []byte("\x89PNG\r\n\x1a\n")

// Receiver stores screenshots POSTed by the tablet into Dir.
type Receiver struct {
	Dir   string
	Allow map[string]bool // peer IPs allowed to post
	Now   func() time.Time
}

func (rv *Receiver) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	if !rv.Allow[host] {
		log.Printf("refused %s: not in -allow", host)
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "POST a PNG", http.StatusMethodNotAllowed)
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxShotBytes))
	if err != nil || !bytes.HasPrefix(data, pngMagic) {
		http.Error(w, "body is not a PNG (or too large)", http.StatusBadRequest)
		return
	}
	name, err := rv.store(data, rv.taken(r.Header.Get(takenHeader)))
	if err != nil {
		log.Printf("store failed: %v", err)
		http.Error(w, "store failed", http.StatusInternalServerError)
		return
	}
	log.Printf("saved %s (%d kB) from %s", name, len(data)/1024, host)
	fmt.Fprintln(w, name)
}

// taken parses the capture time; a missing or implausible value falls back to now.
func (rv *Receiver) taken(h string) time.Time {
	now := rv.Now()
	ns, err := strconv.ParseInt(h, 10, 64)
	if err != nil {
		return now
	}
	t := time.Unix(0, ns)
	if t.After(now.Add(24*time.Hour)) || t.Before(now.AddDate(-1, 0, 0)) {
		return now
	}
	return t
}

// store writes data as rm_shot_YYYYMMDD-HHMMSS.png. A retry of an already stored shot
// (same name, same bytes) is accepted without a second copy; a different shot taken in the
// same second gets a _2, _3 ... suffix.
func (rv *Receiver) store(data []byte, taken time.Time) (string, error) {
	base := "rm_shot_" + taken.Local().Format("20060102-150405")
	for i := 1; i < 100; i++ {
		name := base + ".png"
		if i > 1 {
			name = fmt.Sprintf("%s_%d.png", base, i)
		}
		path := filepath.Join(rv.Dir, name)
		old, err := os.ReadFile(path)
		if err == nil {
			if bytes.Equal(old, data) {
				return name, nil
			}
			continue
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		tmp := filepath.Join(rv.Dir, "."+name+".tmp")
		if err := os.WriteFile(tmp, data, 0o644); err != nil {
			return "", err
		}
		if err := os.Chtimes(tmp, taken, taken); err != nil {
			return "", err
		}
		return name, os.Rename(tmp, path)
	}
	return "", fmt.Errorf("too many shots named %s", base)
}
