# Review and validation record

Reviewed on 2026-10-16. Initial repository state: `706d03e`; working tree clean.
No independent local changes needed preservation. The repository contained only
a license, Git attributes and one inline GitHub Actions workflow. The applicable
user-supplied AGENTS instructions requested context-appropriate standard tooling;
there were no additional repository AGENTS files.

## Decisions from the review

| Existing behavior | Decision |
| --- | --- |
| Debian trixie, amd64, mmdebstrap minbase | Retain; Debian 13 is now stable; pin snapshot inputs |
| Plain HTTP live mirror, mutable Actions references | Use HTTPS, signed APT metadata, pinned bootstrap keyring checksum and Action commits |
| OpenSSH, systemd, nftables, iproute2, wireguard-tools | Retain; include only dependencies and basic provisioning/diagnostic tools |
| ifupdown networking | Retain for Proxmox Debian setup; add explicit DHCP client |
| iptables and curl in the image | Remove explicit inclusion; build-time curl remains on builder only |
| Enabled wg0 plus conditional drop-in | Remove; operator explicitly configures/enables instances |
| Always enabled IPv4/IPv6 forwarding | Remove; routing is an operator choice |
| Masked networkd and custom wait-online links | Disable networkd through normal enablement/presets; rely on packaged ifupdown ordering |
| Terraform-oriented read-only WireGuard mount comment | Remove; no tool-specific management model |
| Tag push creates a release | Replace with published-release build and upload; manual builds do not publish |
| No identity cleanup or artifact inspection | Clear identities, keys, caches/logs; inspect finished archive before upload |

No custom agent, configuration schema or runtime reconciler existed in this
checkout; none has been introduced. scripts/ contains builder/validation tools
only. Runtime customization is limited to ordinary config files, systemd presets
and two drop-ins for Debian's existing SSH host-key service.

## Executed locally

- Bash syntax and ShellCheck for both build scripts: passed.
- actionlint 1.7.12 for the complete GitHub workflow: passed. Download verified
  against the SHA-256 digest supplied by its GitHub release metadata.
- Python archive-inspector regression tests: passed. These cover a clean fixture
  and rejection of secrets, host identity, passwords, enabled VPN/firewall units,
  forwarding, installation network data, archive traversal and build residue.
  Fixtures test rejection behavior; they are not a built Debian image.
- Debian snapshot resolution, InRelease signature verification, package download
  hash verification and extraction: passed using mmdebstrap 1.5.7's chrootless
  **extract** variant, the pinned timestamp and all three repository pockets.
  No package maintainer scripts ran. This partial package audit is not the full
  minbase image build.
- Bootstrap keyring 2025.1 download checksum verified. All three Action commit
  pins resolved to their documented tags.
- Inspected the actual snapshot's packaged SSH, key-generation, wg-quick,
  networking and nftables units. Confirmed SSH's pre-start `sshd -t`, key
  generation's first-boot condition, wg-quick reload support and nftables'
  global-flush stop behavior.
- Ran the extracted Debian `wg`, `ip`, `nft` and `systemctl --version` in a local
  unprivileged user/network namespace with a temporary filesystem. Creating,
  inspecting and deleting a kernel WireGuard interface succeeded. The routed,
  IPv4 NAT and NAT66 nftables examples passed `nft -c`. Loading/deleting the
  example's own table preserved a separate dummy operator table.
  These checks use the WSL host kernel, not Proxmox; there was no peer handshake
  or traffic test and systemd was not PID 1.
- `git diff --check`: passed before commits.

## Not executed locally

The final root-build entry point was attempted but cannot run here: the sandbox
sets no-new-privileges, and outside it sudo requires interactive authentication.
mmdebstrap's unshare mode also cannot run because newuidmap/newgidmap are absent.
No full configured template archive has therefore been produced or approved by
the archive checker locally. The Actions build remains to be run on a disposable
root-capable runner. No release, push or external Proxmox host change was made.

There is no local PVE/LXC runtime. Container creation, systemd boot, first SSH
connection, identity regeneration across independent containers, real peer
handshakes, IPv4/IPv6 routing/NAT and container reboot persistence remain untested.
This is an explicit validation gap, not a compatibility certification.

## Enforced by the full build / CI

The root build configures packages, applies cleanup, tests `sshd -t` using temporary
host keys, deletes those keys again, checks wg-quick's Bash syntax, compresses the
rootfs, tests zstd integrity and inspects the **finished archive** without extraction.
Checks cover required packages, unwanted runtime/kernel packages, account locks,
private-key material, authorized_keys, SSH host keys, WireGuard configs, service
enablement, forwarding, network identity, machine-id, random seeds, caches/logs,
unsafe member paths, SSH key-generation ordering and a default global firewall
flush. The package manifest and build metadata accompany the archive; SHA256SUMS
covers all three assets. These checks detect defined regressions, not arbitrary
secrets hidden anywhere in arbitrary binary files; inputs remain trusted Debian
packages and reviewed builder code.

## Live acceptance checklist for a disposable Proxmox host

1. Record `pveversion -v`, kernel version, image checksum and storage/network setup.
   Verify host WireGuard/netfilter availability. Use a fresh unprivileged container
   with standard AppArmor and no added nesting/keyctl/TUN features.
2. Import the checksum-verified template, provision an operator public SSH key and
   site-specific network parameters with pct. Confirm boot and SSH through the
   intended management path, not just `pct exec`.
3. Before configuration, inspect `wg show`, `nft list ruleset`, both forwarding
   sysctls and `systemctl is-enabled nftables wg-quick@wg0`. There must be no VPN
   endpoint or template-added firewall/forwarding policy.
4. Create a second independent container. Its machine-id and SSH host-key
   fingerprints must differ. Reboot both; each identity must remain stable.
5. Configure the documented dual-stack peer and optional scoped firewall. Verify
   fresh handshakes and bidirectional traffic; test routed return paths, selected
   forward policy, then optional IPv4 NAT/IPv6 routing independently.
6. Keep a separate dummy nftables table and confirm it survives example service
   start/stop. Check firewall-load failure prevents tunnel startup. Verify no
   privileged container or broad LXC security override was needed.
7. Reboot; verify SSH, host identity, routing sysctls, firewall, wg-quick and real
   peer traffic. Change peer config, reload and stop/start; confirm the managed
   file is not rewritten. Test package updates after a backup.
