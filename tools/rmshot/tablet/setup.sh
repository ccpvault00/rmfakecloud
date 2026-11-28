#!/bin/sh
# rmshot on the reMarkable Paper Pro: four-finger hold -> screenshot -> the PC's rm_shot receiver.
#
# Lives in /home/root/rmshot (persistent) next to the rmshot binary. Run it once, and again after
# every reMarkable OS update:   sh /home/root/rmshot/setup.sh
#
# Like rmfakecloud's setup.sh it writes the units to the real root fs (survives reboots until the
# next OS update) AND to the live /etc overlay (whose upper layer is tmpfs), then starts them.
#
# Also installs dropbear-lo: SSH on 127.0.0.1 only, which is where userspace Tailscale delivers
# incoming connections, so the PC can reach the tablet over Tailscale without ssh-over-wlan.
set -eu

BASE=/home/root/rmshot
DEST=http://100.125.113.28:18474/shot   # rmshot recv on the PC (its Tailscale IP)
PROXY=http://127.0.0.1:1055             # tailscaled --outbound-http-proxy-listen (rmfakecloud setup.sh)
LOWER=/run/rmshot-lower

log() { echo "[rmshot] $*"; }

[ -x "$BASE/rmshot" ] || { log "missing $BASE/rmshot"; exit 1; }

mkdir -p "$LOWER"
mountpoint -q "$LOWER" || mount --bind / "$LOWER"
trap 'umount "$LOWER" 2>/dev/null || true' EXIT

write_units() {
    sd="$1/etc/systemd/system"
    mkdir -p "$sd/multi-user.target.wants" "$sd/sockets.target.wants"

    cat > "$sd/rmshot.service" <<EOF
[Unit]
Description=rmshot: four-finger hold takes a screenshot and sends it to the PC
After=home.mount tailscaled.service
RequiresMountsFor=/home

[Service]
ExecStart=$BASE/rmshot daemon -dest $DEST -proxy $PROXY -outbox $BASE/outbox
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    cat > "$sd/dropbear-lo.socket" <<'EOF'
[Unit]
Description=SSH on loopback (reached through userspace Tailscale)

[Socket]
ListenStream=127.0.0.1:22
BindToDevice=lo
Accept=yes

[Install]
WantedBy=sockets.target
EOF

    cat > "$sd/dropbear-lo@.service" <<'EOF'
[Unit]
Description=SSH Per-Connection Server (loopback)
Wants=dropbearkey.service
After=dropbearkey.service

[Service]
EnvironmentFile=-/etc/default/dropbear
Environment=K=/etc/dropbear/dropbear_ed25519_host_key
ExecStart=-/usr/sbin/dropbear -G root -i -r ${K} $DROPBEAR_EXTRA_ARGS
StandardInput=socket
KillMode=process
EOF

    ln -sf ../rmshot.service "$sd/multi-user.target.wants/rmshot.service"
    ln -sf ../dropbear-lo.socket "$sd/sockets.target.wants/dropbear-lo.socket"
}

log "units -> real root fs and live /etc"
write_units "$LOWER"
write_units ""

log "start"
systemctl daemon-reload
systemctl start dropbear-lo.socket
systemctl restart rmshot.service
sleep 2
systemctl is-active rmshot.service dropbear-lo.socket
log "done; hold four fingers for a second to take a screenshot (log: journalctl -u rmshot)"
