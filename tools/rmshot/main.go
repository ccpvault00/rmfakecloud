// Command rmshot takes screenshots of a reMarkable Paper Pro and delivers them to a PC.
//
//	rmshot daemon  (tablet) four-finger hold -> capture -> outbox -> POST to the PC
//	rmshot grab    (tablet) capture once into a PNG file
//	rmshot recv    (PC)     accept the POSTs and save rm_shot_*.png into a folder
package main

import (
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func main() {
	log.SetFlags(0)
	if len(os.Args) < 2 {
		usage()
	}
	var err error
	switch os.Args[1] {
	case "daemon":
		err = runDaemon(os.Args[2:])
	case "grab":
		err = runGrab(os.Args[2:])
	case "recv":
		err = runRecv(os.Args[2:])
	default:
		usage()
	}
	if err != nil {
		log.Fatalf("rmshot %s: %v", os.Args[1], err)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: rmshot daemon|grab|recv [flags]   (-h for the flags)")
	os.Exit(2)
}

func xochitlPID() (string, error) {
	dirs, _ := filepath.Glob("/proc/[0-9]*")
	for _, d := range dirs {
		comm, err := os.ReadFile(filepath.Join(d, "comm"))
		if err == nil && strings.TrimSpace(string(comm)) == "xochitl" {
			return filepath.Base(d), nil
		}
	}
	return "", fmt.Errorf("xochitl is not running")
}

// grabNow captures the running xochitl's screen into the outbox.
func grabNow(o *Outbox) (string, error) {
	pid, err := xochitlPID()
	if err != nil {
		return "", err
	}
	maps, err := os.Open("/proc/" + pid + "/maps")
	if err != nil {
		return "", err
	}
	defer maps.Close()
	mem, err := os.Open("/proc/" + pid + "/mem")
	if err != nil {
		return "", err
	}
	defer mem.Close()
	taken := time.Now()
	img, err := grabScreen(maps, mem)
	if err != nil {
		return "", err
	}
	return o.Save(img, taken)
}

func runGrab(args []string) error {
	fs := flag.NewFlagSet("grab", flag.ExitOnError)
	dir := fs.String("dir", ".", "write the PNG into this folder")
	_ = fs.Parse(args)
	name, err := grabNow(&Outbox{Dir: *dir})
	if err == nil {
		log.Printf("wrote %s", name)
	}
	return err
}

// touchDevice finds the touchscreen's event node by its name.
func touchDevice(name string) (string, error) {
	names, _ := filepath.Glob("/sys/class/input/event*/device/name")
	for _, n := range names {
		b, err := os.ReadFile(n)
		if err == nil && strings.TrimSpace(string(b)) == name {
			return "/dev/input/" + filepath.Base(filepath.Dir(filepath.Dir(n))), nil
		}
	}
	return "", fmt.Errorf("no input device named %q", name)
}

type inputEvent struct {
	typ, code uint16
	value     int32
}

// readEvents streams 64-bit struct input_event records (16-byte timeval + type, code, value).
// The reader only listens; it never grabs the device, so xochitl keeps every touch.
func readEvents(r io.Reader, out chan<- inputEvent) error {
	var b [24]byte
	for {
		if _, err := io.ReadFull(r, b[:]); err != nil {
			return err
		}
		out <- inputEvent{binary.LittleEndian.Uint16(b[16:]), binary.LittleEndian.Uint16(b[18:]), int32(binary.LittleEndian.Uint32(b[20:]))}
	}
}

func runDaemon(args []string) error {
	fs := flag.NewFlagSet("daemon", flag.ExitOnError)
	dest := fs.String("dest", "", "receiver URL on the PC, e.g. http://100.125.113.28:18474/shot (required)")
	proxy := fs.String("proxy", "", "HTTP proxy to reach the tailnet (userspace tailscaled: http://127.0.0.1:1055)")
	dir := fs.String("outbox", "/home/root/rmshot/outbox", "screenshots wait here until the PC accepted them")
	devName := fs.String("touch", "Elan touch input", "name of the touchscreen input device")
	fingers := fs.Int("fingers", 4, "fingers to hold")
	hold := fs.Duration("hold", time.Second, "how long to hold them")
	retry := fs.Duration("retry", time.Minute, "retry interval while shots are waiting")
	lightDir := fs.String("light", "/sys/class/backlight/rm_frontlight", "front light to blink: 1 = captured, 2 = on the PC, 3 = capture failed (\"\" = off)")
	_ = fs.Parse(args)
	if *dest == "" {
		return fmt.Errorf("-dest is required")
	}
	client := &http.Client{Timeout: 60 * time.Second}
	if *proxy != "" {
		u, err := url.Parse(*proxy)
		if err != nil {
			return fmt.Errorf("-proxy: %w", err)
		}
		client.Transport = &http.Transport{Proxy: http.ProxyURL(u)}
	}
	ob := &Outbox{Dir: *dir, Dest: *dest, Client: client}
	var light *Light
	if *lightDir != "" {
		light = &Light{Dir: *lightDir, Pulse: 350 * time.Millisecond}
	}
	blink := func(n int) {
		if err := light.Blink(n); err != nil {
			log.Printf("blink: %v", err)
		}
	}

	dev, err := touchDevice(*devName)
	if err != nil {
		return err
	}
	f, err := os.Open(dev)
	if err != nil {
		return err
	}
	events := make(chan inputEvent, 256)
	readErr := make(chan error, 1)
	go func() { readErr <- readEvents(f, events) }()

	kick := make(chan struct{}, 1)
	go func() { // one sender at a time; kicked after each shot and every -retry
		for range kick {
			if n, err := ob.Flush(); err != nil {
				log.Printf("delivery pending (%d sent): %v", n, err)
			} else if n > 0 {
				log.Printf("delivered %d shot(s)", n)
				blink(2)
			}
		}
	}()
	poke := func() {
		select {
		case kick <- struct{}{}:
		default:
		}
	}
	poke() // anything left from before a reboot

	det := NewDetector(*fingers, *hold)
	tick := time.NewTicker(*retry)
	defer tick.Stop()
	log.Printf("watching %s for a %d-finger hold of %s; shots go to %s", dev, *fingers, *hold, *dest)
	for {
		var timer <-chan time.Time
		if at, ok := det.Deadline(); ok {
			timer = time.After(time.Until(at))
		}
		fired := false
		select {
		case ev := <-events:
			fired = det.Event(time.Now(), ev.typ, ev.code, ev.value)
		case <-timer:
			fired = det.Tick(time.Now())
		case <-tick.C:
			if p, _ := ob.Pending(); len(p) > 0 {
				poke()
			}
		case err := <-readErr:
			return fmt.Errorf("read %s: %w", dev, err)
		}
		if fired {
			start := time.Now()
			name, err := grabNow(ob)
			if err != nil {
				log.Printf("capture failed: %v", err)
				blink(3)
				continue
			}
			log.Printf("captured %s in %s", filepath.Base(name), time.Since(start).Round(time.Millisecond))
			blink(1)
			poke()
		}
	}
}

func runRecv(args []string) error {
	fs := flag.NewFlagSet("recv", flag.ExitOnError)
	listen := fs.String("listen", "", "address to listen on, e.g. 100.125.113.28:18474 (required)")
	allow := fs.String("allow", "", "comma-separated peer IPs allowed to post (required)")
	dir := fs.String("dir", "", "folder the screenshots go to (required)")
	_ = fs.Parse(args)
	if *listen == "" || *allow == "" || *dir == "" {
		return fmt.Errorf("-listen, -allow and -dir are required")
	}
	if st, err := os.Stat(*dir); err != nil || !st.IsDir() {
		return fmt.Errorf("-dir %s is not a folder", *dir)
	}
	rv := &Receiver{Dir: *dir, Allow: map[string]bool{}, Now: time.Now}
	for _, ip := range strings.Split(*allow, ",") {
		rv.Allow[strings.TrimSpace(ip)] = true
	}
	mux := http.NewServeMux()
	mux.Handle("/shot", rv)
	srv := &http.Server{Addr: *listen, Handler: mux, ReadTimeout: 2 * time.Minute}
	log.Printf("receiving on %s into %s (allowed: %s)", *listen, *dir, *allow)
	return srv.ListenAndServe()
}
