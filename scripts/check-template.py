#!/usr/bin/env python3
"""Inspect a template without extracting it or executing image contents."""
import pathlib
import re
import subprocess
import sys
import tarfile


REPO = pathlib.Path(__file__).resolve().parents[1]
BUILD_CONF = (REPO / 'build.conf').read_text()
ALPINE_VERSION = re.search(r'^ALPINE_VERSION=([0-9.]+)$', BUILD_CONF, re.M)[1]
ALPINE_BRANCH = re.search(r'^ALPINE_BRANCH=(v[0-9.]+)$', BUILD_CONF, re.M)[1]
LOCKED_PACKAGES = {}
for line in (REPO / 'packages.lock').read_text().splitlines():
    if not line or line.startswith('#'):
        continue
    checksum, filename = line.split()
    if not re.fullmatch(r'[a-f0-9]{64}', checksum):
        raise ValueError('Invalid package lock checksum')
    match = re.fullmatch(r'(.+)-([0-9][^-]*-r[0-9]+)\.apk', filename)
    if not match or match[1] in LOCKED_PACKAGES:
        raise ValueError('Invalid or duplicate package lock entry')
    LOCKED_PACKAGES[match[1]] = match[2]


REQUIRED = {
    'alpine-base', 'openrc', 'openssh', 'openssh-server',
    'openssh-server-common-openrc', 'openssh-keygen', 'wireguard-tools',
    'wireguard-tools-wg', 'wireguard-tools-wg-quick', 'wireguard-tools-openrc',
    'nftables', 'nftables-openrc', 'iproute2', 'ifupdown-ng', 'ca-certificates',
}
RUNLEVELS = {
    'boot': ('bootmisc', 'hostname', 'networking', 'sysctl', 'machine-id'),
    'default': ('sshd', 'syslog'),
    'shutdown': ('killprocs', 'savecache'),
}


def check(stream):
    files = {}
    names = set()
    links = {}
    errors = []
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            path = pathlib.PurePosixPath(member.name)
            name = str(path)
            if path.is_absolute() or '..' in path.parts or name in names:
                errors.append(f'Unsafe or duplicate member: {member.name}')
            names.add(name)
            if member.issym():
                links[name] = member.linkname
            if member.isfile():
                if name.startswith(('etc/', 'root/', 'home/', 'usr/lib/sysctl.d/', 'lib/sysctl.d/')) or name == 'lib/apk/db/installed':
                    files[name] = archive.extractfile(member).read()
            if re.search(r'(^|/)(ssh_host_[^/]+|authorized_keys2?|id_rsa|id_ed25519|random-seed|\.bash_history|\.ash_history)$', name):
                errors.append(f'Identity or access material: {name}')
            if name.startswith('etc/wireguard/') and not member.isdir():
                errors.append(f'WireGuard configuration present: {name}')
            if name.startswith(('tmp/', 'var/tmp/', 'var/log/', 'dev/', 'run/', 'var/cache/apk/', 'var/lib/seedrng/')) and not member.isdir():
                errors.append(f'Build residue: {name}')
            if '/var/lib/dbus/machine-id' == '/' + name:
                errors.append('D-Bus machine identity present')
            if name.startswith('etc/runlevels/') and not member.isdir():
                level, _, service = name.removeprefix('etc/runlevels/').partition('/')
                if service not in RUNLEVELS.get(level, ()):
                    errors.append(f'Unexpected enabled OpenRC service: {name}')
            if name.startswith(('etc/systemd/', 'usr/lib/systemd/', 'etc/apt/', 'var/lib/dpkg/')):
                errors.append(f'Previous distribution residue: {name}')
    if files.get('etc/resolv.conf') != b'':
        errors.append('resolv.conf must be an empty regular file')
    if 'etc/machine-id' in names:
        errors.append('Machine identity present')
    if files.get('etc/alpine-release', b'').strip() != ALPINE_VERSION.encode():
        errors.append('Unexpected Alpine release')
    if files.get('etc/network/interfaces') != b'auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n':
        errors.append('Unexpected network configuration')
    if any(n.startswith('etc/network/interfaces.d/') for n in files):
        errors.append('Installation-specific interface configuration')
    if files.get('etc/hostname') != b'localhost\n':
        errors.append('Unexpected hostname')
    if files.get('etc/hosts') != b'127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n':
        errors.append('Unexpected hosts file')
    for name, data in files.items():
        if name.startswith(('etc/', 'root/', 'home/')) and (
            b'PRIVATE KEY-----' in data or re.search(rb'^\s*(PrivateKey|PresharedKey)\s*=', data, re.M)
        ):
            errors.append(f'Private key material: {name}')
        if (name == 'etc/sysctl.conf' or name.startswith(('etc/sysctl.d/', 'usr/lib/sysctl.d/', 'lib/sysctl.d/'))) and re.search(
            rb'^\s*(?:net[./]ipv4[./](?:ip_forward|conf[./][\w-]+[./]forwarding)|net[./]ipv6[./]conf[./][\w-]+[./]forwarding)\s*=\s*[1-9]', data, re.M
        ):
            errors.append(f'Forwarding enabled: {name}')
        if name in ('etc/shadow', 'etc/shadow-'):
            for line in data.decode().splitlines():
                fields = line.split(':')
                if len(fields) < 2 or not re.fullmatch(r'[!*]+', fields[1]):
                    errors.append(f'Unlocked account or password in {name}')
    if not re.search(rb'^root:\*:', files.get('etc/shadow', b''), re.M):
        errors.append('Missing Alpine root no-password marker (*)')
    if files.get('etc/nftables.nft') != b'# No rules installed. Configure explicitly before enabling nftables.\n':
        errors.append('Unexpected default firewall configuration')
    if any(n.startswith('etc/nftables.d/') and data.strip() for n, data in files.items()):
        errors.append('Firewall example present')
    for level, services in RUNLEVELS.items():
        for service in services:
            path = f'etc/runlevels/{level}/{service}'
            if links.get(path) != f'/etc/init.d/{service}':
                errors.append(f'Missing packaged OpenRC service link: {path}')
    if b'rc_sys="lxc"' not in files.get('etc/rc.conf', b''):
        errors.append('OpenRC LXC mode missing')
    inittab = files.get('etc/inittab', b'')
    for command in (b'::sysinit:/sbin/openrc sysinit', b'::sysinit:/sbin/openrc boot',
                    b'::wait:/sbin/openrc default', b'::shutdown:/sbin/openrc shutdown'):
        if command not in inittab:
            errors.append('Missing BusyBox/OpenRC init entry')
    sshd = files.get('etc/init.d/sshd', b'')
    if b'generate_host_keys' not in sshd or b'ssh-keygen -A' not in sshd:
        errors.append('Missing packaged SSH host-key generation')
    if re.search(rb'^\s*sshd_disable_keygen=["\']?yes', files.get('etc/conf.d/sshd', b''), re.M):
        errors.append('SSH host-key generation disabled')
    status = files.get('lib/apk/db/installed', b'').decode()
    versions = {}
    for block in status.split('\n\n'):
        fields = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        if 'P' in fields:
            versions[fields['P']] = fields.get('V')
            if fields.get('A') not in ('x86_64', 'noarch'):
                errors.append('Unexpected package architecture')
    packages = set(versions)
    if versions != LOCKED_PACKAGES:
        errors.append('Installed package versions differ from packages.lock')
    if files.get('etc/apk/repositories') != f'https://dl-cdn.alpinelinux.org/alpine/{ALPINE_BRANCH}/main\n'.encode():
        errors.append('Unexpected runtime repositories')
    if REQUIRED - packages:
        errors.append(f'Missing packages: {sorted(REQUIRED - packages)}')
    if any(re.match(r'(python\d|dart|linux-(lts|virt|edge)|systemd|dpkg)', p) for p in packages):
        errors.append('Unnecessary runtime, kernel or previous distribution package')
    # world must permit normal apk upgrade; local APK installs otherwise pin checksums.
    if re.search(rb'[=<>~@]', files.get('etc/apk/world', b'')):
        errors.append('Pinned runtime world prevents normal security updates')
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
