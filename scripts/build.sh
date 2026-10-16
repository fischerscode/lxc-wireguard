#!/bin/bash
# Build-time tooling only; nothing from scripts/ is installed into the image.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=./build.conf
source ./build.conf
repo=$PWD
version=${1:-dev}
[[ $version =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid version' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'Run on a disposable amd64 Linux builder as root (sudo).' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'An x86_64 builder is required.' >&2; exit 1; }
for tool in curl gpg gpgv zstd python3 git chroot tar; do command -v "$tool" >/dev/null; done
export LC_ALL=C TZ=UTC
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p dist "$work/rootfs" "$work/gnupg"
chmod 700 "$work/gnupg"
export GNUPGHOME=$work/gnupg
archive="alpine-minirootfs-${ALPINE_VERSION}-${ARCH}.tar.gz"
base="$MIRROR/$ALPINE_BRANCH/releases/$ARCH/$archive"
curl --fail --location --retry 5 --connect-timeout 20 --max-time 300 "$base" -o "$work/$archive"
curl --fail --location --retry 5 --connect-timeout 20 --max-time 300 "$base.asc" -o "$work/$archive.asc"
printf '%s  %s\n' "$MINIROOTFS_SHA256" "$work/$archive" | sha256sum -c -
fingerprint=$(gpg --batch --show-keys --with-colons keys/alpine-release.asc | awk -F: '$1 == "fpr" { print $10; exit }')
[[ $fingerprint == "$RELEASE_KEY_FINGERPRINT" ]]
gpg --batch --dearmor -o "$work/release-key.gpg" keys/alpine-release.asc
gpgv --keyring "$work/release-key.gpg" "$work/$archive.asc" "$work/$archive"
root=$work/rootfs
tar --numeric-owner -xzf "$work/$archive" -C "$root"
mkdir -p "$root/packages"
# Download only locked APKs. A removed version fails instead of selecting a replacement.
while read -r checksum filename extra; do
    [[ -z $checksum || $checksum == \#* ]] && continue
    [[ $checksum =~ ^[a-f0-9]{64}$ && $filename =~ ^[a-zA-Z0-9+_.-]+\.apk$ && -z $extra ]]
    curl --fail --location --retry 5 --connect-timeout 20 --max-time 300 \
        "$MIRROR/$ALPINE_BRANCH/main/$ARCH/$filename" -o "$root/packages/$filename"
done < packages.lock
(cd "$root/packages" && sha256sum -c "$repo/packages.lock")
# APK rechecks Alpine package signatures using the keys from the verified minirootfs.
# A cache is required by APK 3 for local packages; no untrusted/force flags are used.
chroot "$root" /bin/sh -ec 'apk verify /packages/*.apk; apk --cache-dir /packages --no-network add /packages/*.apk'
printf 'https://dl-cdn.alpinelinux.org/alpine/%s/main\n' "$ALPINE_BRANCH" > "$root/etc/apk/repositories"
# Local-file installation pins checksums in world. Restore normal upgradeable roots.
read -r -a world_packages <<< "$PACKAGES"
printf '%s\n' "${world_packages[@]}" > "$root/etc/apk/world"
rm -rf "$root/packages"
scripts/customize.sh "$root"
python3 - "$root/lib/apk/db/installed" > "dist/packages.tmp" <<'PY'
import sys
from pathlib import Path
rows = []
for block in Path(sys.argv[1]).read_text().split('\n\n'):
    fields = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
    if 'P' in fields:
        rows.append((fields['P'], fields['V'], fields['A']))
for row in sorted(rows):
    print('\t'.join(row))
PY
name="alpine-${ALPINE_VERSION}-wireguard-${ARCH}_${version}"
mv dist/packages.tmp "dist/$name.packages.tsv"
{
    printf 'version=%s\ncommit=%s\nalpine=%s\narchitecture=%s\nsource_date_epoch=%s\n' \
        "$version" "$(git rev-parse HEAD)" "$ALPINE_VERSION" "$ARCH" "$SOURCE_DATE_EPOCH"
    printf 'working_tree_clean=%s\n' "$(test -z "$(git status --porcelain --untracked-files=normal)" && echo yes || echo no)"
    printf 'minirootfs_sha256=%s\nrelease_key=%s\n' "$MINIROOTFS_SHA256" "$RELEASE_KEY_FINGERPRINT"
    sha256sum packages.lock
    chroot "$root" apk --version
    tar --version | head -n 1
    zstd --version
} > "dist/$name.build-info.txt"
install -D -m 644 "dist/$name.build-info.txt" "$root/usr/share/doc/lxc-wireguard/build-info.txt"
# Checkconfig needs device nodes; they are build-only and removed before packing.
mknod -m 666 "$root/dev/null" c 1 3
mknod -m 666 "$root/dev/urandom" c 1 9
# Use Alpine's own key generation and configuration check, then remove test keys.
chroot "$root" rc-service sshd checkconfig
rm -f "$root"/etc/ssh/ssh_host_*
chroot "$root" /bin/bash -n /usr/bin/wg-quick
for service in sshd wg-quick nftables networking sysctl machine-id; do
    chroot "$root" /bin/sh -n "/etc/init.d/$service"
done
# OpenRC's checkconfig may create temporary runtime state; none belongs in a template.
find "$root/dev" "$root/run" "$root/tmp" "$root/var/log" -mindepth 1 -delete
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --numeric-owner --xattrs --acls \
    -C "$root" -cf - . | zstd -T1 -19 -o "dist/$name.tar.zst" -f
zstd -t "dist/$name.tar.zst"
python3 scripts/check-template.py "dist/$name.tar.zst"
(cd dist && sha256sum "$name.tar.zst" "$name.packages.tsv" "$name.build-info.txt" > SHA256SUMS)
