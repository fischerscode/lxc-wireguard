#!/bin/bash
# Build-time tooling only; nothing from scripts/ is installed into the image.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=./build.conf
source ./build.conf
version=${1:-dev}
[[ $version =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid version' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'Run on a disposable Debian/Ubuntu builder as root (sudo).' >&2; exit 1; }
for tool in mmdebstrap zstd python3 git chroot curl dpkg-deb; do command -v "$tool" >/dev/null; done
export LC_ALL=C TZ=UTC
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p dist
# Bootstrap trust is pinned independently of old builder-distribution keyrings.
curl --fail --location --retry 5 \
    "https://deb.debian.org/debian/pool/main/d/debian-archive-keyring/debian-archive-keyring_${KEYRING_VERSION}_all.deb" \
    -o "$work/keyring.deb"
printf '%s  %s\n' "$KEYRING_SHA256" "$work/keyring.deb" | sha256sum -c -
dpkg-deb -x "$work/keyring.deb" "$work/keyring"
name="debian-13-wireguard-${ARCH}_${version}"
mmdebstrap --mode=root --variant=minbase --architectures="$ARCH" \
    --keyring="$work/keyring/usr/share/keyrings/debian-archive-keyring.gpg" \
    --aptopt='Acquire::Retries "5"' \
    --aptopt='Acquire::Check-Valid-Until "false"' \
    --aptopt='Acquire::Languages "none"' \
    --aptopt='APT::Install-Recommends "false"' \
    --include=ca-certificates,debian-archive-keyring,systemd-sysv,openssh-server,wireguard-tools,nftables,iproute2,procps,ifupdown,isc-dhcp-client,iputils-ping \
    "$SUITE" "$work/rootfs" \
    "deb https://snapshot.debian.org/archive/debian/$SNAPSHOT/ $SUITE main" \
    "deb https://snapshot.debian.org/archive/debian/$SNAPSHOT/ $SUITE-updates main" \
    "deb https://snapshot.debian.org/archive/debian-security/$SNAPSHOT/ $SUITE-security main"
root=$work/rootfs
scripts/customize.sh "$root"
# dpkg-query expands these fields, not the shell.
# shellcheck disable=SC2016
chroot "$root" dpkg-query -W '-f=${Package}\t${Version}\t${Architecture}\n' > "dist/$name.packages.tsv"
{
    printf 'version=%s\ncommit=%s\nsuite=%s\narchitecture=%s\nsnapshot=%s\nsource_date_epoch=%s\n' \
        "$version" "$(git rev-parse HEAD)" "$SUITE" "$ARCH" "$SNAPSHOT" "$SOURCE_DATE_EPOCH"
    printf 'working_tree_clean=%s\n' "$(test -z "$(git status --porcelain --untracked-files=normal)" && echo yes || echo no)"
    printf 'keyring_version=%s\nkeyring_sha256=%s\n' "$KEYRING_VERSION" "$KEYRING_SHA256"
    mmdebstrap --version
    dpkg-deb --version | head -n 1
    tar --version | head -n 1
    zstd --version
} > "dist/$name.build-info.txt"
install -D -m 644 "dist/$name.build-info.txt" "$root/usr/share/doc/lxc-wireguard/build-info.txt"
# The test creates disposable keys; remove them before packaging.
chroot "$root" ssh-keygen -A
mkdir -p "$root/run/sshd"
chroot "$root" /usr/sbin/sshd -t
rm -f "$root"/etc/ssh/ssh_host_*
rm -rf "$root/run/sshd"
chroot "$root" /bin/bash -n /usr/bin/wg-quick
# Preserve ownership and capabilities. Never follow rootfs symlinks into the host.
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --numeric-owner --xattrs --acls \
    -C "$root" -cf - . | zstd -T1 -19 -o "dist/$name.tar.zst" -f
zstd -t "dist/$name.tar.zst"
python3 scripts/check-template.py "dist/$name.tar.zst"
(cd dist && sha256sum "$name.tar.zst" "$name.packages.tsv" "$name.build-info.txt" > SHA256SUMS)
