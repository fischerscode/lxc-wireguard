"""Regression checks for security-relevant template rejection paths."""
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import tarfile
import unittest

spec = importlib.util.spec_from_file_location('checker', pathlib.Path(__file__).parents[1] / 'scripts/check-template.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class TemplateTests(unittest.TestCase):
    def image(self, extra=None):
        files = {
            'etc/resolv.conf': '',
            'etc/alpine-release': checker.ALPINE_VERSION+'\n',
            'etc/rc.conf': 'rc_sys="lxc"\n',
            'etc/inittab': '::sysinit:/sbin/openrc sysinit\n::sysinit:/sbin/openrc boot\n::wait:/sbin/openrc default\n::shutdown:/sbin/openrc shutdown\n',
            'etc/init.d/sshd': 'generate_host_keys() { ssh-keygen -A; }\n',
            'etc/apk/world': 'alpine-base\nopenssh\n',
            'etc/apk/repositories': f'https://dl-cdn.alpinelinux.org/alpine/{checker.ALPINE_BRANCH}/main\n',
            'etc/nftables.nft': '# No rules installed. Configure explicitly before enabling nftables.\n',
            'etc/hostname': 'localhost\n',
            'etc/hosts': '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n',
            'etc/network/interfaces': 'auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n',
            'etc/shadow': 'root:*:20000:0:99999:7:::\n',
            'lib/apk/db/installed': '\n\n'.join(f'P:{p}\nV:{v}\nA:x86_64' for p, v in checker.LOCKED_PACKAGES.items()),
        }
        files.update(extra or {})
        links = {f'etc/runlevels/{level}/{service}': f'/etc/init.d/{service}'
                 for level, services in checker.RUNLEVELS.items() for service in services}
        for path in (extra or {}):
            links.pop(path, None)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as archive:
            for name, text in files.items():
                data = text.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            for name, target in links.items():
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)
        output.seek(0)
        return output

    def test_clean_image(self):
        checker.check(self.image())

    def test_rejects_secrets_and_identity(self):
        for name, value in {
            'etc/ssh/ssh_host_ed25519_key': 'key',
            'root/.ssh/authorized_keys': 'ssh-ed25519 example',
            'etc/machine-id': '0123456789abcdef0123456789abcdef',
            'etc/shadow': 'root:$6$hash:20000:0:99999:7:::\n',
            'etc/shadow-': 'root:!$6$hash:20000:0:99999:7:::\n',
            'etc/hidden.key': '-----BEGIN PRIVATE KEY-----',
        }.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.check(self.image({name: value}))

    def test_rejects_active_configuration(self):
        for name, value in {
            'etc/wireguard/wg0.conf': '[Interface]',
            'etc/runlevels/default/wg-quick.wg0': '',
            'etc/runlevels/boot/nftables': '',
            'etc/sysctl.d/router.conf': 'net.ipv4.ip_forward = 1',
            'etc/sysctl.d/router6.conf': 'net/ipv6/conf/all/forwarding = 1',
            'usr/lib/sysctl.d/router.conf': 'net.ipv4.ip_forward = 1',
            'etc/nftables.nft': 'flush ruleset',
            'etc/apk/world': 'openssh=10.3_p1-r1',
            'lib/apk/db/installed': 'P:openssh\nV:0-r0\nA:x86_64',
            'etc/conf.d/sshd': 'sshd_disable_keygen=yes',
            'etc/network/interfaces.d/eth0': 'address 192.0.2.1',
        }.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.check(self.image({name: value}))

    def test_rejects_residue_and_unsafe_archive_names(self):
        for name in ('var/log/build.log', 'tmp/key', '../escape', '/etc/escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.check(self.image({name: 'unexpected'}))

    def test_compressed_archive_cli_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / 'template.tar.zst'
            subprocess.run(['zstd', '-q', '-o', str(archive)], input=self.image().read(), check=True)
            command = [sys.executable, str(pathlib.Path(checker.__file__)), str(archive)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            archive.write_bytes(archive.read_bytes()[:32])
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_requires_packaged_services(self):
        with self.assertRaises(ValueError):
            checker.check(self.image({'etc/runlevels/default/sshd': ''}))


if __name__ == '__main__':
    unittest.main()
