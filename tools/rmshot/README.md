# rmshot — screenshots from the reMarkable Paper Pro

Hold **four fingers** on the screen for a second: the tablet captures the screen (colour, 1620×2160,
toolbar included) and sends it over Tailscale to this PC, which saves it as
`~/Dropbox/imgtmp/rm_shot_YYYYMMDD-HHMMSS.png` (named and dated by the capture time).

The front light tells you how it went (each blink is full brightness, then off; your level is restored):
**1 blink** captured, **2 blinks** on the PC, **3 blinks** capture failed. A shot that could not be
delivered blinks twice later, when it finally arrives.

```
tablet: rmshot daemon ── touch (read-only) ─► capture from xochitl's memory ─► outbox/
                         └─ POST via tailscaled's HTTP proxy (127.0.0.1:1055) ─► PC: rmshot recv ─► imgtmp/
```

- **Capture.** The Paper Pro has no framebuffer device and no screenshot function. xochitl keeps the
  composed screen as a 1632×2160 BGRA image (12 padding columns) in a malloc chunk whose glibc header
  holds the size word `0xd72ff2`; `rmshot` finds that chunk in `/proc/<xochitl>/mem` and converts it
  (0.6 s). Found on OS 3.29.1 by inspecting the memory; a later OS may change it (`capture failed:
  screen buffer not found` in the log). Portrait only — landscape is untested.
- **Gesture.** Four fingers down together for ≥ 1 s, once per touch. xochitl ignores four-finger
  holds; the daemon only reads the touchscreen, so xochitl still gets every touch.
  `testdata/paperpro_gestures.bin` is a real recording (two holds fire; a three-finger hold and quick taps do not).
- **Delivery.** Shots wait in `/home/root/rmshot/outbox` until the PC answers 2xx, retried every
  minute, so a shot taken offline arrives later. The receiver accepts only the tablet's Tailscale IP,
  and a resend of a stored shot does not make a second copy.

## Install

Build (no Go needed on the host):

```bash
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/src -w /src golang:bookworm sh -c \
  'CGO_ENABLED=0 GOARCH=arm64 go build -trimpath -ldflags=-s -o dist/rmshot-arm64 . && CGO_ENABLED=0 go build -trimpath -ldflags=-s -o dist/rmshot-amd64 .'
```

PC: `cp dist/rmshot-amd64 ~/.local/bin/rmshot`, then install `rmshot-recv.service` (instructions in
its header; edit the IPs if they change).

Tablet: copy `dist/rmshot-arm64` to `/home/root/rmshot/rmshot` and `tablet/setup.sh` to
`/home/root/rmshot/setup.sh`, then on the tablet `sh /home/root/rmshot/setup.sh`.
**Re-run it after every reMarkable OS update** (like rmfakecloud's setup.sh). It also adds
`dropbear-lo`, SSH on the tablet's loopback, which is where userspace Tailscale delivers incoming
connections: `ssh root@paperpro` then works from the tailnet without `rm-ssh-over-wlan`.

Manual capture on the tablet: `/home/root/rmshot/rmshot grab -dir /tmp`. Logs: `journalctl -u rmshot`
(tablet), `journalctl --user -u rmshot-recv` (PC).

## Test

```bash
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/src -w /src golang:bookworm go test ./...
```

`RMSHOT_MAPPING=<dump>@<hexaddr>` additionally runs the capture on a real dump of one xochitl mapping.
