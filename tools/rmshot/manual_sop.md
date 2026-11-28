# rmshot manual SOP (needs the Paper Pro and this PC on the tailnet)

Prerequisites: `rmshot-recv` running on the PC (`systemctl --user status rmshot-recv`), `sh
/home/root/rmshot/setup.sh` run on the tablet (`systemctl is-active rmshot` → `active`), tablet awake.
Logs: `journalctl -u rmshot -f` (tablet), `journalctl --user -u rmshot-recv -f` (PC).

| ID | Steps | Expected |
|----|-------|----------|
| RS-01 | Open a page, hold four fingers ~1 s | 1 blink, then 2 blinks; tablet log `captured <ns>.png in ~600ms`, `delivered 1 shot(s)`; PC log `saved rm_shot_YYYYMMDD-HHMMSS.png (~130 kB)`; the PNG is the screen in colour with the toolbar, 1620×2160 |
| RS-02 | Hold three fingers 2 s; tap four fingers quickly | no blink, no file; xochitl unaffected |
| RS-03 | Keep four fingers down 5 s | exactly one shot |
| RS-04 | `systemctl --user stop rmshot-recv`, take a shot, start it again | 1 blink only; `delivery pending … 502` (or connection error) in the tablet log; within a minute of the restart 2 blinks and the file named after the *capture* time |
| RS-05 | Light off, take a shot; light at a mid level, take a shot | every blink is full brightness then off; afterwards the light is back where it was |
| RS-06 | `ssh root@100.102.185.106 true` from the PC (Wi-Fi SSH off) | succeeds (dropbear-lo) |
| RS-07 | After a reMarkable OS update, before re-running setup.sh | no blink (units gone); after `sh /home/root/rmshot/setup.sh` RS-01 passes, or the log says `screen buffer not found` (new OS changed the buffer → re-inspect memory) |

Known failure modes: a new OS version or landscape orientation (untested) may move or reshape the
buffer — capture fails with 3 blinks rather than sending a wrong image; the receiver's Tailscale IP
must match `DEST` in `tablet/setup.sh`.
