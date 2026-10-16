# Alpine migration and validation record

Reviewed on 2026-10-16, starting from `da9ab1f` with a clean working tree. No
independent changes needed preservation. The user's Alpine/OpenRC request replaces
the earlier Debian/systemd requirement. No additional repository AGENTS rules
were present; context-appropriate standard tooling was used.

## Implementation decisions

- Replace Debian/minbase/mmdebstrap with Alpine's official 3.24.2 x86_64 minirootfs
  and regular main-repository packages. Use BusyBox init, OpenRC and ifupdown-ng.
  No guest kernel, Python, Dart or management agent is installed.
- Pin bootstrap version/hash/release-signing fingerprint, vendor its public key
  and lock every APK filename/hash. Verify GPG, HTTPS and APK signatures using
  standard tools. Install the complete locked closure offline. Missing upstream
  versions cause failure; they are never silently upgraded at build time.
- Keep the installed `/etc/apk/world` unpinned and the stable main repository live
  for normal security updates. Local-file APK installation otherwise leaves
  checksum constraints in world; the build deliberately removes those constraints.
- Remove Debian/systemd presets, SSH drop-ins and APT/Dpkg build logic. Use the
  unmodified packaged OpenRC services for SSH keys, machine-id, sysctl and network.
  Select OpenRC's normal LXC mode and ordinary runlevel links. No custom init
  service or periodic configuration job is added.
- Keep Alpine's root `*` shadow marker, not an empty password or a new password
  hash. Proxmox's inspected root-password setup also uses `*` when no password is
  supplied. SSH public-key access is provisioned by the operator through PVE.
- Clear identity, keys, resolver data, temporary device nodes, runtime state,
  logs, caches and downloaded APKs. No tunnel or firewall service is enabled;
  no forwarding configuration is added. Replace nftables' active example with
  a comment-only file. All optional deployment examples stay in documentation.
- Preserve the existing published-release asset workflow, pinned action commits
  and narrow upload-job permissions; replace builder dependencies with the tools
  needed for Alpine. No push or release publication is performed.

## Executed locally

- Bash syntax, ShellCheck and actionlint: passed.
- Six Python inspector regression tests: passed, including zstd CLI success and
  truncation rejection. They exercise secret/identity/password checks, forbidden
  OpenRC services, required service links, forwarding, network state, runtime
  APK pins, package-version drift, disabled SSH key generation and unsafe paths.
  These fixtures are not a production image.
- Downloaded minirootfs 3.24.2 x86_64, compared its SHA-256 with Alpine's release
  checksum and the committed pin, and verified its detached signature against
  the pinned release key: passed. Verified the minimized vendored public key too.
- Resolved packages against the signature-checked v3.24 main index and fetched
  the full 62-package dependency closure. All 62 APKs passed native `apk verify`;
  the lock file records their actual SHA-256 hashes. No community/edge packages
  were needed. APK 3 uses a package cache for local installation; disabling the
  cache caused a refusal, so the build uses a temporary cache without bypass flags.
- Installed those packages, including their normal scripts/triggers, into a
  temporary chroot in a user namespace, and exercised the customization script.
  This staging tree was unpacked without preserving ownership and is therefore
  **not** a production image or a complete build validation. After applying
  production cleanup and restoring the upgradeable world file, the actual staging
  tree's streamed tar contents passed the inspector (all 62 exact package versions,
  no shipped keys/configuration/runtime residue). Ownership was not validated.
- Inspected the actual packaged sshd, wg-quick, networking, sysctl, machine-id and
  nftables service/configuration files, plus PVE's Alpine and password setup code.
  Confirmed native SSH missing-key generation, WireGuard instance naming/reload,
  sysctl.d support, machine-id generation and nftables' global flush on stop.
- Ran Alpine's unmodified `rc-service sshd checkconfig` with temporary device
  mounts: host keys were generated and sshd configuration validation succeeded.
  A repeated check preserved existing host keys. BusyBox shell syntax checks for
  the native services and Bash syntax for wg-quick passed. Test keys are removed
  by the production build before packaging.
- Ran the actual Alpine nftables binary in an isolated local network namespace.
  Routed, IPv4 NAT and NAT66 documentation examples passed `nft -c`; applying the
  table twice and deleting it preserved a separate dummy operator table. This
  verifies the example file's scope, not the native service's global stop behavior.
- Ran two Alpine wg-quick peers in separate temporary network namespaces on the
  WSL kernel. A fresh handshake and IPv4/IPv6 tunnel pings succeeded; both peers
  were torn down. This tested terminating tunnel traffic, not routed/NAT traffic.
  Normal LXC-provided /proc and /dev/fd were supplied temporarily for wg-quick.
- `git diff --check`: passed before commits.

## Not executed / limits

The full production build entry point was attempted but refuses the local
non-root execution. Inside the sandbox sudo cannot elevate due to
no-new-privileges; outside it sudo requires interactive authentication. The local
single-ID user namespace cannot preserve the original root:shadow ownership and
create the production device nodes. Partial installation tests do not remove
that limitation. No complete production `.tar.zst` was built or checked locally,
and **no complete-image compressed or unpacked size is reported**.

There is no local Proxmox/LXC runtime. PVE import, OpenRC boot as PID 1, first SSH
login, independent-container machine identity, routing/NAT traffic and reboot
persistence remain untested. The WSL namespace tests are not Proxmox compatibility
certification. No external Proxmox host was accessed or changed.

## Checks enforced by the production build

After signature/hash-checked offline installation and cleanup, the build runs
Alpine's SSH checkconfig with temporary host keys, removes them, checks wg-quick
and OpenRC service syntax, tests zstd integrity and inspects the finished archive
without extracting it. The inspector checks exact locked package versions,
architecture, required packages, forbidden runtimes/kernel packages, root shadow
state, secrets, authorized keys, machine identities, enabled runlevels, inittab,
LXC mode, native SSH key generation, sysctls, networking, empty firewall policy,
caches and runtime residue. It rejects Debian/systemd leftovers as a migration
regression. Metadata/manifests accompany the archive, covered by SHA256SUMS.

Checks cover defined regressions, not arbitrary secrets concealed in arbitrary
binary files. Inputs remain trusted Alpine packages and reviewed build sources.
The complete pipeline still needs a run on a disposable root-capable builder.

## Outstanding live acceptance checks

1. Record PVE/kernel versions and image checksum. Import into two fresh
   unprivileged containers with normal AppArmor, no nesting/keyctl/TUN additions,
   explicit site networking and different identities provisioned from the image.
2. Verify OpenRC boot, first public-key SSH login, network/DNS and logs. Before
   configuration check no VPN interface, enabled nftables instance, template
   rules or forwarding policy exists.
3. Confirm both containers have different host-key fingerprints/machine-id, and
   each retains its own identity after reboot. Check SSH key regeneration only
   occurs when keys are absent.
4. Configure the documented dual-stack tunnel and appropriate firewall ownership.
   Test real routed return paths, restricted forwarding and IPv4 NAT/IPv6 routing
   independently. Test startup failure when the required firewall fails.
5. Reboot, then verify SSH, sysctls, firewall, wg-quick and real peer traffic.
   Exercise reload/restart and confirm externally managed files are not rewritten.
6. Back up, apply `apk update`/`apk upgrade`, review service restarts and repeat
   relevant tests. Do not assume a new template updates existing containers.
