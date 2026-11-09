"""Synthetic packaging checks; no commercial backup or Apple build is needed."""
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_ipa_from_3ds as packer


class Packer(unittest.TestCase):
    def test_private_paths_cannot_use_repository_or_nas(self):
        for path in (packer.REPO, packer.REPO / "private", Path("/Volumes/ZNAS/private")):
            with self.assertRaises(packer.DumpError):
                packer.outside_repo(path)

    def test_filename_uses_portable_title(self):
        self.assertEqual(packer.ipa_name("Pokémon Omega Ruby"), "PokemonOmegaRuby")
        self.assertEqual(packer.ipa_name("../"), "3DSGame")

    def test_pack_preserves_executable_mode_and_omits_signatures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Example.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Example"}))
            binary = app / "Example"
            binary.write_bytes(b"synthetic executable")
            binary.chmod(0o755)
            signature = app / "_CodeSignature"
            signature.mkdir()
            (signature / "CodeResources").write_bytes(b"synthetic signature")
            output = root / "Example.ipa"
            with patch.object(packer.subprocess, "run") as inspect:
                inspect.return_value.returncode = 1
                packer.pack(app, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"Payload/Example.app/Example", "Payload/Example.app/Info.plist"})
                self.assertTrue(archive.getinfo("Payload/Example.app/Example").external_attr >> 16 & 0o111)
            with patch.object(packer.subprocess, "run") as inspect:
                inspect.return_value.returncode = 0
                with self.assertRaises(packer.DumpError):
                    packer.pack(app, output)
