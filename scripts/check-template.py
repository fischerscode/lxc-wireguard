#!/usr/bin/env python3
"""Inspect a template without extracting it or executing image contents."""
import pathlib
import re
import subprocess
import sys
import tarfile


REQUIRED = {
    'systemd', 'systemd-sysv', 'openssh-server', 'wireguard-tools', 'nftables',
    'iproute2', 'procps', 'ifupdown', 'isc-dhcp-client', 'ca-certificates',
    'debian-archive-keyring',
}


def check(stream):
    files = {}
    names = set()
    errors = []
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            path = pathlib.PurePosixPath(member.name)
            name = str(path)
            if path.is_absolute() or '..' in path.parts or name in names:
                errors.append(f'Unsafe or duplicate member: {member.name}')
            names.add(name)
            if member.isfile():
                if name.startswith(('etc/', 'root/', 'home/', 'usr/lib/sysctl.d/')) or name == 'var/lib/dpkg/status':
                    files[name] = archive.extractfile(member).read()
            if re.search(r'(^|/)(ssh_host_[^/]+|authorized_keys2?|id_rsa|id_ed25519|random-seed|\.bash_history)$', name):
                errors.append(f'Identity or access material: {name}')
            if name.startswith('etc/wireguard/') and not member.isdir():
                errors.append(f'WireGuard configuration present: {name}')
            if name.startswith(('tmp/', 'var/tmp/', 'var/log/', 'var/lib/apt/lists/', 'var/cache/apt/archives/', 'var/lib/dhcp/')) and not member.isdir():
                errors.append(f'Build residue: {name}')
            if '/var/lib/dbus/machine-id' == '/' + name:
                errors.append('D-Bus machine identity present')
            if ('/system/' in name and ('.wants/' in name or '.requires/' in name)
                    and re.search(r'/(wg-quick@[^/]+|nftables|ssh\.socket|systemd-networkd(?:-wait-online)?\.(?:service|socket))', name)):
                errors.append(f'Unwanted enabled service: {name}')
    for name in ('etc/machine-id', 'etc/resolv.conf'):
        if files.get(name) != b'':
            errors.append(f'{name} must be an empty regular file')
    if files.get('etc/network/interfaces') != b'auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n':
        errors.append('Unexpected network configuration')
    if any(n.startswith('etc/network/interfaces.d/') for n in files):
        errors.append('Installation-specific interface configuration')
    if files.get('etc/hostname') != b'localhost\n':
        errors.append('Unexpected hostname')
    if files.get('etc/hosts') != b'127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n':
        errors.append('Unexpected hosts file')
    if any(b'Check-Valid-Until' in data and b'false' in data for name, data in files.items() if name.startswith('etc/apt/')):
        errors.append('Snapshot expiry exception in deployed image')
    for name, data in files.items():
        if name.startswith(('etc/', 'root/', 'home/')) and (
            b'PRIVATE KEY-----' in data or re.search(rb'^\s*(PrivateKey|PresharedKey)\s*=', data, re.M)
        ):
            errors.append(f'Private key material: {name}')
        if (name == 'etc/sysctl.conf' or name.startswith(('etc/sysctl.d/', 'usr/lib/sysctl.d/'))) and re.search(
            rb'^\s*(?:net[./]ipv4[./](?:ip_forward|conf[./][\w-]+[./]forwarding)|net[./]ipv6[./]conf[./][\w-]+[./]forwarding)\s*=\s*[1-9]', data, re.M
        ):
            errors.append(f'Forwarding enabled: {name}')
        if name in ('etc/shadow', 'etc/shadow-'):
            for line in data.decode().splitlines():
                fields = line.split(':')
                if len(fields) < 2 or not re.fullmatch(r'[!*]+', fields[1]):
                    errors.append(f'Unlocked account or password in {name}')
    if 'etc/shadow' not in files:
        errors.append('Missing shadow file')
    if b'flush ruleset' in files.get('etc/nftables.conf', b''):
        errors.append('Global firewall flush in default configuration')
    if 'etc/systemd/system/multi-user.target.wants/ssh.service' not in names:
        errors.append('SSH not enabled')
    if files.get('etc/systemd/system/sshd-keygen.service.d/template.conf') != b'[Unit]\nConditionFirstBoot=\n':
        errors.append('Missing SSH host-key generation')
    if b'Requires=sshd-keygen.service\nAfter=sshd-keygen.service' not in files.get('etc/systemd/system/ssh.service.d/host-keys.conf', b''):
        errors.append('Missing SSH host-key ordering')
    status = files.get('var/lib/dpkg/status', b'').decode()
    packages = set()
    for entry in status.split('\n\n'):
        package = re.search(r'^Package: (.+)$', entry, re.M)
        if package and 'Status: install ok installed' in entry:
            packages.add(package[1])
    if REQUIRED - packages:
        errors.append(f'Missing packages: {sorted(REQUIRED - packages)}')
    if any(re.match(r'(python\d|dart|linux-image|wireguard-dkms)', p) for p in packages):
        errors.append('Unnecessary runtime or kernel package')
    if errors:
        raise ValueError('\n'.join(errors))
    return len(packages)


def main():
    with subprocess.Popen(['zstd', '-dc', '--', sys.argv[1]], stdout=subprocess.PIPE) as process:
        try:
            count = check(process.stdout)
        except (ValueError, tarfile.TarError) as error:
            # Drain the pipe so decompressor errors remain visible.
            process.stdout.read()
            print(error, file=sys.stderr)
            return 1
        process.stdout.read()
        if process.wait() != 0:
            return 1
    print(f'Template checks passed ({count} installed packages).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
