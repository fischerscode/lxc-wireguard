# WireGuard LXC template for Proxmox

A small Debian 13 (trixie), amd64 root filesystem with OpenSSH, wireguard-tools,
nftables, iproute2 and systemd. Configure it over SSH or `pct exec` with ordinary
Debian files and tools. There is no management agent, configuration API, runtime
reconciler, Python or Dart configuration dependency.

The template contains no VPN configuration, peers, credentials, SSH host keys or
machine identity. Root is locked; SSH public-key provisioning is required.
Forwarding is not enabled. nftables and wg-quick instances are not enabled.
Nothing installs firewall rules at boot. Examples below are documentation only.

## Compatibility and host requirements

Target: **fully updated Proxmox VE 9.x, amd64**, using an **unprivileged** container
and the standard Debian OS setup. This is the supported deployment target, not a
claim of live certification. No Proxmox boot, networking or reboot test has yet
been performed for this revision. Older PVE releases, other architectures and
non-Proxmox LXC are outside the supported target. Keep `pve-container` current:
old versions can reject newer Debian point-release identifiers.

Containers share the host kernel. On the **Proxmox host**, check/load WireGuard:

```sh
modprobe wireguard
```

The host kernel must provide WireGuard, network namespaces and nftables/netfilter
(including connection tracking and NAT when used). It must allow the container's
namespaced `CAP_NET_ADMIN` for interfaces, routes and rules. Default unprivileged
PVE networking is the intended starting point. No guest kernel, headers, DKMS,
`/dev/net/tun`, nesting or keyctl feature is needed for kernel WireGuard.

Do not grant host `CAP_SYS_MODULE`, use a privileged container or disable AppArmor
to solve a module problem. Load needed kernel modules on the host. Host firewall,
bridge policies, upstream routing and UDP port forwarding are separate from the
guest firewall. Custom LXC capability drops, seccomp/AppArmor policies or sysctl
restrictions can prevent operation; diagnose the specific denial. The template
cannot change these host policies. Userspace wireguard-go is not included.

These expectations follow the [Proxmox container guide](https://pve.proxmox.com/pve-docs/chapter-pct.html)
and [Debian setup implementation](https://github.com/proxmox/pve-container/blob/master/src/PVE/LXC/Setup/Debian.pm).
Debian 13 is the [current stable release](https://www.debian.org/releases/trixie/).

## Install and establish SSH access

Download the `.tar.zst` and `SHA256SUMS` from the same published GitHub release.
Review the source/release you trust, then verify the archive before importing it:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

Ensure the archive itself reports `OK`; the checksum detects corruption but does
not independently authenticate a compromised release. Copy it to a Proxmox
storage supporting `vztmpl` (for `local`, normally `/var/lib/vz/template/cache/`).
On the host, adapt storage, bridge, address and ID to your site:

```sh
pct create 120 local:vztmpl/debian-13-wireguard-amd64_v1.0.0.tar.zst \
  --ostype debian --arch amd64 --unprivileged 1 \
  --hostname wireguard --rootfs local-lvm:2 --memory 256 --swap 0 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,ip6=auto \
  --ssh-public-keys /root/provisioning.pub
pct start 120
pct exec 120 -- ip -br address
pct exec 120 -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

`/root/provisioning.pub` must contain **your public SSH key**, never a private key.
Proxmox installs it into root's authorized_keys. No password is embedded in the
image. Debian's root SSH policy permits keys and prohibits password login. Verify
the displayed host-key fingerprint through this trusted host console, then:

```sh
ssh root@CONTAINER_ADDRESS
```

Alternatively use `pct exec` to install a public key with directory mode 0700 and
file mode 0600 in `/root/.ssh/authorized_keys` before exposing SSH. Provision your
usual administrator account and SSH policy as needed. A locked root password does
not prevent `pct exec`. Do not enable empty-password login.

Proxmox supplies hostname, DNS and `/etc/network/interfaces`. ifupdown and a DHCP
client are included for dynamic addresses; use static IPv4/IPv6 in `pct create`
when appropriate. systemd-networkd is disabled to avoid a second network manager.
The image clears `/etc/machine-id`; systemd generates it on boot. Proxmox normally
creates SSH host keys at provisioning, with Debian's `sshd-keygen.service`
required before SSH starts. A drop-in clears its first-boot condition so missing
keys are generated even if provisioning already populated machine-id. Keys survive subsequent boots.
Cloning an already provisioned container copies identities: use the clean template
for new containers, or deliberately sanitize a stopped clone before reuse.

## Configure WireGuard

See [the minimal dual-stack setup](docs/configuration.md) for wg-quick, routed
forwarding, optional NAT, scoped nftables rules and boot persistence. You can use
`wg` directly instead; persistence and address/route setup then remain your own
responsibility. Configuration belongs to the operator, whether managed manually,
by Ansible or by another SSH-based tool. No tool-specific protocol is required.

## Build and release

On a disposable amd64 Debian/Ubuntu VM with root and working mount namespaces:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends mmdebstrap curl ca-certificates \
  zstd python3 git shellcheck
bash -n scripts/build.sh scripts/customize.sh
shellcheck -x scripts/build.sh scripts/customize.sh
python3 -m unittest discover -s tests -v
sudo scripts/build.sh dev
```

`dist/` contains the archive, installed-package versions, build metadata and
SHA256SUMS. Build tooling uses Python only to inspect archives; Python is not
installed in the image. The build requires root and trusted repository scripts;
never run it on a production Proxmox host. Temporary rootfs data is deleted on
exit. `dist/` is ignored by Git.

[build.conf](build.conf) pins the suite, architecture, Debian snapshot timestamp
and bootstrap keyring download checksum. APT verifies signed repository metadata
and package hashes; authentication is never disabled. Expiry checking is relaxed
only for historical snapshot metadata during the build. Deployed containers use
normal Debian stable, updates and security repositories with normal verification.
Refresh the snapshot and keyring deliberately, rebuild and review the package
manifest for security updates. Snapshots do not automatically receive new fixes.

The archive has sorted entries, a single compression thread and Git-derived
archive timestamps. Package versions and source revision are recorded. Host
mmdebstrap/dpkg/tar versions and package-generated content can still differ;
this is an auditable build, not a promise of byte-identical output on any host.
The Actions runner OS and action commits are pinned; builder packages receive OS
updates. Preserve build logs and manifests when comparing builds.

GitHub Actions builds/checks on main pushes, pull requests, manual runs and every
**published GitHub release** (including prereleases). Only the separate upload job
has `contents: write`; it attaches the tested archive, manifests and SHA256SUMS to
the existing release. It never creates a release from a pushed tag. A published
release temporarily has no assets until the build succeeds; inspect failed runs
and rerun them. Existing assets are not silently overwritten. GitHub release
events created using another workflow's `GITHUB_TOKEN` do not trigger workflows;
publish manually or use an appropriately scoped GitHub App/token for that future
automation. Keep the workflow on the default branch and on the release tag.

No release is published by the build scripts. Local builds and manual Actions runs
produce artifacts without publishing.

## Update existing containers

A new template is only used when creating a new container. It does not update an
existing one. Back up the container and configuration, then run inside it:

```sh
apt-get update
apt-get upgrade
```

Review Debian security updates and service restarts. Reboot the container when
appropriate; shared-kernel updates require a separate Proxmox host maintenance
window. Major Debian upgrades follow Debian's release notes, not template import.

For an old experimental image, audit `/etc/sysctl.d/99-wg.conf`, enabled
`wg-quick@wg0`, custom unit drop-ins and the active firewall before changing them.
They may now be intentional operator configuration. Migrate deliberately or
provision a fresh container, transfer only reviewed configuration and generate new
identities. Do not run the build cleanup script against an installed container.

## Verification status

See [docs/validation.md](docs/validation.md) for the checks actually performed,
limitations and the live-test checklist. No external Proxmox host is modified by
this repository's checks.
