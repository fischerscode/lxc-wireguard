#!/bin/bash
# Integration check on a disposable Alpine rootfs, never on a running container.
# Kernel changes are confined to a new network namespace; nothing is installed.
set -euo pipefail
root=$(realpath "${1:?disposable Alpine rootfs required}")
[[ $root != / && -f $root/etc/alpine-release ]]
unshare --net --mount bash -s -- "$root" <<'TEST'
set -euo pipefail
root=$1
work=$(mktemp -d)
override=$root/etc/sysctl.d/90-forwarding-test.conf
vendor_mask=$root/run/sysctl.d/00-alpine.conf
[[ ! -e $override && ! -e $vendor_mask ]]
cp -a "$root/etc/network/interfaces" "$work/interfaces"
trap 'cp -a "$work/interfaces" "$root/etc/network/interfaces"; rm -f "$override" "$vendor_mask"; rmdir "$root/run/sysctl.d" 2>/dev/null || true; rm -rf "$work"' EXIT
# The vendor file also sets non-namespaced kernel/fs controls and ping GID ranges.
# Mask it via OpenRC's standard /run override, only inside this disposable test.
# Its shipped contents are still covered by the static archive inspection.
mkdir -p "$root/run/sysctl.d"
: > "$vendor_mask"
# Keep /proc's existing security submounts when binding into a user namespace.
mount --rbind /proc "$root/proc"
mount --bind /dev/null "$root/dev/null"
# Exercise the packaged sysctl start function with real BusyBox sysctl writes.
# OpenRC display/boolean helpers are stubbed: this is not an OpenRC/PVE boot test.
apply_sysctls() {
    chroot "$root" /bin/sh -c '
        . /etc/init.d/sysctl
        ebegin() { :; }; eindent() { :; }; eoutdent() { :; }
        vebegin() { :; }; veinfo() { :; }; vewarn() { :; }
        eerror() { echo "$*" >&2; }; eend() { return "$1"; }
        yesno() { test "$1" = yes; }
        rc_verbose=yes
        start
    '
}
expect() {
    local expected=$1 key
    for key in net.ipv4.ip_forward net.ipv4.conf.default.forwarding \
        net.ipv6.conf.all.forwarding net.ipv6.conf.default.forwarding; do
        [[ $(chroot "$root" sysctl -n "$key") == "$expected" ]] || {
            echo "Unexpected $key (expected $expected)" >&2; exit 1;
        }
    done
}
cat > "$root/etc/network/interfaces" <<'INTERFACES'
auto lo
iface lo inet loopback
auto eth0
iface eth0 inet static
    address 198.18.0.2/24
iface eth0 inet6 static
    address 2001:db8:1::2/64
INTERFACES
chroot "$root" ip link add eth0 type veth peer name test-peer
chroot "$root" ip link set test-peer up
for cycle in 1 2; do
    chroot "$root" sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 \
        net.ipv6.conf.default.forwarding=1
    apply_sysctls
    expect 0
    timeout 30 chroot "$root" ifup -a
    expect 0
    timeout 30 chroot "$root" ifdown -a
    expect 0
done
# A later operator-owned file wins over the baseline during normal startup.
printf 'net.ipv4.ip_forward = 1\nnet.ipv4.conf.default.forwarding = 1\nnet.ipv6.conf.all.forwarding = 1\nnet.ipv6.conf.default.forwarding = 1\n' > "$override"
apply_sysctls
expect 1
timeout 30 chroot "$root" ifup -a
expect 1
timeout 30 chroot "$root" ifdown -a
expect 1
echo 'Forwarding defaults, network reconfiguration and operator override passed (isolated namespace).'
TEST
