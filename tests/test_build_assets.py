import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('assets', BASE/'scripts/check-assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        runtime = self.base/'assets/ollama-runtime'
        (runtime/'bin').mkdir(parents=True)
        (runtime/'lib/ollama').mkdir(parents=True)
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 183)
        (runtime/'bin/ollama').write_bytes(header)
        (runtime/'lib/ollama/libggml-cpu.so').write_bytes(b'test fixture, not a runtime')
        (self.base/'settings').mkdir()
        (self.base/'settings/device.json').write_text('{}')
        self.models = self.base/'assets/ollama-models'
        self.manifest = self.models/'manifests/registry.ollama.ai/library/whiteboard-metadata/latest'
        self.manifest.parent.mkdir(parents=True)
        parts = []
        for kind, body in [('config', b'config fixture'), ('model', b'model fixture')]:
            digest = hashlib.sha256(body).hexdigest()
            blob = self.models/'blobs'/('sha256-'+digest)
            blob.parent.mkdir(exist_ok=True)
            blob.write_bytes(body)
            parts.append({'digest':'sha256:'+digest, 'mediaType':'application/vnd.ollama.image.'+kind})
        self.manifest.write_text(json.dumps({'config':parts[0], 'layers':[parts[1]]}))

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_store_checksums_are_accepted(self):
        self.assertEqual(assets.check(self.base), [])

    def test_windows_or_x86_executable_is_rejected(self):
        (self.base/'assets/ollama-runtime/bin/ollama').write_bytes(b'MZ Windows fixture')
        with self.assertRaisesRegex(SystemExit, 'ARM64'):
            assets.check(self.base)

    def test_tampered_model_blob_is_rejected(self):
        next((self.models/'blobs').iterdir()).write_bytes(b'tampered')
        with self.assertRaisesRegex(SystemExit, 'checksum mismatch'):
            assets.check(self.base)

    def test_export_copies_only_referenced_blobs(self):
        extra = self.models/'blobs/unrelated'
        extra.write_bytes(b'not required')
        destination = self.base/'export'
        subprocess.run([sys.executable, str(BASE/'scripts/export-model.py'),
                        '--source',str(self.models),'--destination',str(destination)],check=True,
                       capture_output=True)
        self.assertFalse((destination/'blobs/unrelated').exists())
        self.assertEqual(len(list((destination/'blobs').iterdir())), 2)


class NetworkSettingsTests(unittest.TestCase):
    def test_keyfile_cannot_add_another_section(self):
        spec = importlib.util.spec_from_file_location('settings', BASE/'scripts/install-settings.py')
        # Run the installer against a temporary project/settings tree to test real serialization.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scripts = root/'scripts'; scripts.mkdir()
            (scripts/'install-settings.py').write_bytes((BASE/'scripts/install-settings.py').read_bytes())
            (root/'settings').mkdir()
            (root/'settings/device.json').write_text(json.dumps({
                'ssh_public_key':'ssh-ed25519 publicfixture',
                'country':'US','ssid':'name with spaces;semi',
                'wifi_password':'password\n[injected]\nvalue=evil'}))
            target=root/'target';target.mkdir()
            subprocess.run([sys.executable,str(scripts/'install-settings.py'),str(target)],
                           check=True,capture_output=True)
            profile=target/'etc/NetworkManager/system-connections/whiteboard-wifi.nmconnection'
            text=profile.read_text()
            self.assertNotIn('\n[injected]\n',text)
            self.assertIn(r'name\swith\sspaces\;semi',text)
            self.assertEqual(profile.stat().st_mode & 0o777,0o600)
