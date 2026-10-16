# WireGuard LXC template for Proxmox

A small **Alpine Linux 3.24.2, x86_64** root filesystem using BusyBox init and
OpenRC. OpenSSH, wireguard-tools (including wg-quick), nftables and iproute2 are
installed from Alpine's official main repository. Configure ordinary files over
SSH or `pct exec`, manually or with external management tools. There is no custom
management agent, configuration protocol, reconciler, Python or Dart runtime.

The image ships no VPN configuration, peers, private keys, passwords, authorized
SSH keys or machine identity. No WireGuard instance, nftables rules or forwarding
is enabled. Since v0.1.1, `/etc/sysctl.d/10-wireguard-defaults.conf` explicitly
sets IPv4/IPv6 forwarding to zero, including the defaults for new interfaces.
Operators can opt into routing with a later sysctl file; nothing periodically
resets these values. Optional examples exist only in this repository's documentation.

## Compatibility and host requirements

Supported deployment target: **fully updated Proxmox VE 9.x on amd64**, with an
**unprivileged** container and standard Alpine OS setup. This is the intended
support scope, not a live certification. An operator reported a successful
v0.1.0 Proxmox boot with OpenRC, sshd and sysctl running, but IPv4 forwarding was
unexpectedly 1 before VPN configuration. Its cause is unconfirmed. The v0.1.1
fix still needs fresh-container and reboot acceptance tests on Proxmox. Other
PVE versions, architectures and generic LXC installations are outside the supported target.

The container shares the **host** kernel. On the Proxmox host, verify/load kernel
WireGuard (`modprobe wireguard`) and the required nftables/netfilter features.
Connection tracking and NAT support are needed when using those features.
The container needs namespaced `CAP_NET_ADMIN` to manage its interfaces, routes
and firewall. Start with standard unprivileged PVE permissions. Kernel WireGuard
needs no guest kernel, headers, DKMS, `/dev/net/tun`, nesting or keyctl option.

Do not use a privileged container, disable AppArmor or grant host module-loading
capabilities to solve a missing host module. Diagnose specific denials when using
custom capability, seccomp, AppArmor or sysctl restrictions. PVE firewall, bridge
policies, upstream routes and UDP port forwarding are separate host/site concerns.
No build or validation command in this repository changes an external host.

Alpine uses ifupdown-ng and `/etc/network/interfaces`; BusyBox provides the DHCPv4
client. Proxmox supplies addresses, DNS and hostname during provisioning.
The [PVE Alpine setup implementation](https://github.com/proxmox/pve-container/blob/master/src/PVE/LXC/Setup/Alpine.pm)
currently converts `ip6=auto` and `ip6=dhcp` to manual configuration. Prefer explicit
static IPv6 addresses/routes for predictable dual-stack operation. SLAAC requires
appropriate interface/RA settings; do not assume Proxmox's DHCPv6 setting provisions
a working DHCPv6 client in this template.

## Installation and first SSH access

Download the `.tar.zst` and `SHA256SUMS` from the same trusted GitHub release:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

Ensure the archive itself reports `OK`. A checksum detects corruption; it does not
independently authenticate a compromised release. Copy the archive into a PVE
storage with `vztmpl` support (normally `/var/lib/vz/template/cache/` for `local`).
On your host, adapt ID, storage, bridge and network parameters:

```sh
pct create 120 local:vztmpl/alpine-3.24.2-wireguard-x86_64_v1.0.0.tar.zst \
  --ostype alpine --arch amd64 --unprivileged 1 \
  --hostname wireguard --rootfs local-lvm:2 --memory 256 --swap 0 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,ip6=manual \
  --ssh-public-keys /root/provisioning.pub
pct start 120
pct exec 120 -- ip -br address
pct exec 120 -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

`x86_64` is Alpine's name for PVE's `amd64` architecture. The filename is an example
release version; use the asset you downloaded. `/root/provisioning.pub` contains
your **public** key. Proxmox installs it into `/root/.ssh/authorized_keys`.
Verify the displayed host-key fingerprint through the trusted host console, then
connect with `ssh root@CONTAINER_ADDRESS`.

Alternatively, install your public key through `pct exec` with `/root/.ssh` mode
0700 and `authorized_keys` mode 0600. The image preserves Alpine's root `*` shadow
marker (no usable password) and OpenSSH's `prohibit-password` root policy. Never
replace it with an empty password. Alpine OpenSSH does not use PAM here; blindly
changing the marker to `!` may also deny public-key access. Provision administrator
accounts and your own SSH policy as needed. `pct exec` remains a recovery path.

The unmodified packaged `sshd` OpenRC service generates missing host keys before
checking/starting the daemon. Proxmox can also generate them at provisioning;
they are never copied from the build. The packaged `machine-id` service generates
a missing identity at boot. Both identities survive reboots. Cloning an already
provisioned container copies its identities: create new containers from the clean
template, or deliberately sanitize a stopped clone before reuse.

## Configuration and operation

Follow [the optional dual-stack setup](docs/configuration.md) for wg-quick, sysctl,
OpenRC boot persistence, routed operation and optional IPv4 NAT/NAT66. Nothing in
that document is activated by the build. There is no SaveConfig behavior that
rewrites externally managed tunnel files.

Use `rc-status`, `rc-service sshd status`, `wg show`, `ip -br address`,
`ip -4 route`, `ip -6 route`, `nft list ruleset` and `/var/log/messages` to diagnose
problems. BusyBox syslog is enabled. OpenRC's regular LXC mode avoids treating the
container as a physical machine; there are no custom init scripts.

## Build inputs, checks and releases

Build on a disposable root-capable x86_64 Linux machine. For a Debian/Ubuntu
builder (the image itself remains Alpine):

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends curl ca-certificates gnupg \
  zstd python3 git shellcheck
bash -n scripts/build.sh scripts/customize.sh tests/check-forwarding.sh
shellcheck -x scripts/build.sh scripts/customize.sh tests/check-forwarding.sh
python3 -m unittest discover -s tests -v
sudo scripts/build.sh dev
```

[build.conf](build.conf) pins the Alpine branch, minirootfs point release, SHA-256
and full release-signing-key fingerprint. The [vendored public key](keys/alpine-release.asc)
verifies the archive's detached GPG signature. It is Natanael Copa's public key,
fingerprint `0482D84022F52DF1C4E7CD43293ACD0907D9495A`, retrieved by full fingerprint
from the Ubuntu public keyserver and stored locally for review. APK verifies official package
signatures using the Alpine keys inside that verified bootstrap. Checks never
disable TLS certificates or package authentication.

[packages.lock](packages.lock) pins the exact filenames and SHA-256 of the complete
APK dependency closure, including the base packages. APK installation is offline
from those verified files. Official branch repositories are rolling: if an old
APK is removed, the build fails, rather than substituting a new version. Preserve
the locked APKs in your own archival cache/mirror when long-term rebuilds matter;
this repository does not promise Alpine retains every historic package.

Refresh locks deliberately for security fixes. In a disposable, verified Alpine
3.24 minirootfs, use only the configured main repository and run:

```sh
mkdir -p /packages
apk --update-cache fetch --recursive --output /packages \
  alpine-base openssh wireguard-tools wireguard-tools-openrc \
  nftables nftables-openrc iproute2 ca-certificates
apk verify /packages/*.apk
(cd /packages && sha256sum *.apk)
```

Commit the sorted checksum output as packages.lock, review changed versions,
rebuild and run checks. Update bootstrap version/hash/signing key separately when
appropriate. Do not mix edge or another branch. All selected packages are in main;
[Alpine's support schedule](https://www.alpinelinux.org/releases/) lists v3.24 main
support through June 2028. Recheck the schedule when maintaining this template.

`dist/` contains the checked archive, package manifest, build metadata and
SHA256SUMS. No image size is claimed without a complete build. The tar stream is
sorted, Git-timestamped and compressed with one thread. Package-generated data and
builder tool versions can still differ; byte-identical builds are not guaranteed.
Temporary rootfs/downloads are deleted on exit. Python is builder validation only.

GitHub Actions builds on pull requests, main pushes, manual runs and every
**published GitHub release**, including prereleases. Only the separate asset-upload
job has `contents: write`. It verifies checksums and attaches the archive,
manifest, build metadata and SHA256SUMS to the existing release. No release is
created from a tag push and existing assets are not silently overwritten.
A published release has no assets until its build finishes; inspect/rerun failed
runs. Releases created with another workflow's GITHUB_TOKEN do not trigger this
workflow: use manual publication or an appropriate GitHub App/token. Keep the
workflow on both the default branch and release tag.

## Updates and migration

Existing Alpine containers use the normal stable main repository and unpinned
`/etc/apk/world`. After taking a backup, run inside the container:

```sh
apk update
apk upgrade
```

Review service restart needs; for example `rc-service sshd restart` and, in a
maintenance window, `rc-service wg-quick.wg0 restart` if configured. Reboot when
appropriate. Shared-kernel updates belong to a separate PVE host maintenance
window. Major Alpine branch upgrades follow Alpine's upgrade guidance.

A new template never updates an existing container automatically. Existing Debian
containers cannot be converted with apk: provision a fresh Alpine container,
transfer only reviewed operator configuration, generate fresh host/machine
identities and switch routing/endpoints deliberately. Keep a tested rollback.
Do not run build cleanup scripts against installed containers.

[Validation status and outstanding live checks](docs/validation.md) distinguish
local tool tests from a complete template build and Proxmox compatibility tests.

The prepared [v0.1.1 patch release notes](docs/releases/v0.1.1.md) describe the
forwarding fix and pending acceptance checks. Publication requires approval;
v0.1.0 assets must remain unchanged.
