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
            'etc/machine-id': '', 'etc/resolv.conf': '',
            'etc/hostname': 'localhost\n',
            'etc/hosts': '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n',
            'etc/network/interfaces': 'auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n',
            'etc/shadow': 'root:!:20000:0:99999:7:::\n',
            'etc/systemd/system/sshd-keygen.service.d/template.conf': '[Unit]\nConditionFirstBoot=\n',
            'etc/systemd/system/ssh.service.d/host-keys.conf': 'Requires=sshd-keygen.service\nAfter=sshd-keygen.service\n',
            'etc/systemd/system/multi-user.target.wants/ssh.service': '',
            'var/lib/dpkg/status': '\n\n'.join(f'Package: {p}\nStatus: install ok installed' for p in checker.REQUIRED),
        }
        files.update(extra or {})
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as archive:
            for name, text in files.items():
                data = text.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
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
            'etc/systemd/system/multi-user.target.wants/wg-quick@wg0.service': '',
            'etc/systemd/system/sysinit.target.wants/nftables.service': '',
            'etc/sysctl.d/router.conf': 'net.ipv4.ip_forward = 1',
            'etc/sysctl.d/router6.conf': 'net/ipv6/conf/all/forwarding = 1',
            'usr/lib/sysctl.d/router.conf': 'net.ipv4.ip_forward = 1',
            'etc/nftables.conf': 'flush ruleset',
            'etc/apt/apt.conf.d/99mmdebstrap': 'Acquire::Check-Valid-Until \"false\";',
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

    def test_requires_ssh_key_ordering(self):
        with self.assertRaises(ValueError):
            checker.check(self.image({'etc/systemd/system/ssh.service.d/host-keys.conf': ''}))


if __name__ == '__main__':
    unittest.main()
