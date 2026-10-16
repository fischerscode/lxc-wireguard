#!/bin/bash
set -euo pipefail
root=${1:?rootfs required}
[[ -f $root/etc/alpine-release && $root != / ]] || exit 1
mkdir -p "$root/etc/network/interfaces.d" "$root/etc/wireguard"
chmod 700 "$root/etc/wireguard"
# Apply once during the normal OpenRC sysctl boot phase, before networking.
install -D -m 644 "$(dirname "$0")/../files/etc/sysctl.d/10-wireguard-defaults.conf" \
    "$root/etc/sysctl.d/10-wireguard-defaults.conf"
cat > "$root/etc/network/interfaces" <<'INTERFACES'
auto lo
iface lo inet loopback
source /etc/network/interfaces.d/*
INTERFACES
printf 'localhost\n' > "$root/etc/hostname"
printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n' > "$root/etc/hosts"
rm -f "$root/etc/resolv.conf"
touch "$root/etc/resolv.conf"
# OpenRC's regular container mode skips hardware/filesystem services inappropriate in LXC.
sed -i 's/^#\?rc_sys=.*/rc_sys="lxc"/' "$root/etc/rc.conf"
grep -qx 'rc_sys="lxc"' "$root/etc/rc.conf"
# Retain BusyBox init/OpenRC's normal boot and shutdown entries; PVE supplies gettys.
sed -i '/^tty[0-9].*:respawn:/d' "$root/etc/inittab"
cat >> "$root/etc/inittab" <<'INITTAB'
# LXC console; Proxmox also configures its requested tty count.
::respawn:/sbin/getty 38400 console
INITTAB
# Only packaged services; no VPN instance or firewall service is enabled.
for service in bootmisc hostname networking sysctl machine-id; do
    chroot "$root" rc-update add "$service" boot
done
for service in sshd syslog; do chroot "$root" rc-update add "$service" default; done
for service in killprocs savecache; do chroot "$root" rc-update add "$service" shutdown; done
# Replace the package's active example with an empty operator-owned policy.
printf '# No rules installed. Configure explicitly before enabling nftables.\n' > "$root/etc/nftables.nft"
# Alpine ships root:* (no usable password). Preserve this rather than introducing !,
# which OpenSSH without PAM can interpret as a locked account even for public keys.
awk -F: '$1 == "root" && $2 == "*" { found=1 } END { exit !found }' "$root/etc/shadow"
rm -f "$root"/etc/ssh/ssh_host_* "$root/etc/machine-id" "$root/var/lib/dbus/machine-id" \
    "$root"/etc/*- "$root/root/.ash_history" "$root/root/.bash_history"
rm -rf "$root/root/.ssh" "$root/root/.cache" "$root/var/lib/seedrng"
# LXC mounts /dev and /run. Do not ship nodes or any runtime/build state.
for directory in dev run tmp var/tmp var/log var/cache/apk; do
    find "$root/$directory" -mindepth 1 -delete
done
rm -f "$root/etc/apk/cache"
