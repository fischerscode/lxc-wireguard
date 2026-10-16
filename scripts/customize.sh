#!/bin/bash
set -euo pipefail
root=${1:?rootfs required}
[[ -d $root/etc && $root != / ]] || exit 1
# Use live repositories in deployed containers; only build inputs use snapshots.
rm -f "$root"/etc/apt/sources.list.d/*
cat > "$root/etc/apt/sources.list" <<'SOURCES'
deb https://deb.debian.org/debian trixie main
deb https://deb.debian.org/debian trixie-updates main
deb https://security.debian.org/debian-security trixie-security main
SOURCES
# Snapshot expiry relaxation must never carry over into deployed containers.
rm -f "$root/etc/apt/apt.conf.d/99mmdebstrap"
mkdir -p "$root/etc/network/interfaces.d" "$root/etc/wireguard" \
    "$root/etc/systemd/system/ssh.service.d" "$root/etc/systemd/system/sshd-keygen.service.d" "$root/etc/systemd/system-preset"
chmod 700 "$root/etc/wireguard"
cat > "$root/etc/network/interfaces" <<'INTERFACES'
auto lo
iface lo inet loopback
source /etc/network/interfaces.d/*
INTERFACES
# Proxmox configures interfaces and DNS during pct create.
printf 'localhost\n' > "$root/etc/hostname"
printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n' > "$root/etc/hosts"
rm -f "$root/etc/resolv.conf"
touch "$root/etc/resolv.conf"
# Use Debian's key-generation unit even if provisioning populated machine-id.
# ssh-keygen -A creates only missing keys, preserving identity across reboots.
cat > "$root/etc/systemd/system/sshd-keygen.service.d/template.conf" <<'UNIT'
[Unit]
ConditionFirstBoot=
UNIT
cat > "$root/etc/systemd/system/ssh.service.d/host-keys.conf" <<'UNIT'
[Unit]
Requires=sshd-keygen.service
After=sshd-keygen.service
UNIT
# Avoid competing with ifupdown or applying Debian's sample flush-ruleset file.
cat > "$root/etc/systemd/system-preset/00-lxc-wireguard.preset" <<'PRESET'
enable ssh.service
disable ssh.socket
disable nftables.service
disable wg-quick@.service
disable systemd-networkd.service
disable systemd-networkd.socket
disable systemd-networkd-wait-online.service
PRESET
chroot "$root" systemctl disable nftables.service ssh.socket systemd-networkd.service \
    systemd-networkd.socket systemd-networkd-wait-online.service
chroot "$root" systemctl enable ssh.service networking.service
# Keep nftables opt-in; never ship Debian's sample global ruleset flush.
printf '# No rules installed. Configure explicitly before enabling nftables.service.\n' > "$root/etc/nftables.conf"
# No passwords, host keys, cached randomness or machine identity in the template.
chroot "$root" passwd -l root
rm -f "$root"/etc/ssh/ssh_host_* "$root/var/lib/dbus/machine-id" \
    "$root/var/lib/systemd/random-seed" "$root/etc/machine-id" \
    "$root"/etc/*- "$root/usr/sbin/policy-rc.d"
touch "$root/etc/machine-id"
rm -rf "$root/root/.ssh" "$root/root/.cache"
rm -f "$root/root/.bash_history"
find "$root/tmp" "$root/var/tmp" "$root/var/log" "$root/var/cache/apt/archives" \
    "$root/var/lib/apt/lists" "$root/var/lib/dhcp" -mindepth 1 -delete
