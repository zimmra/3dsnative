"""Synthetic container fixtures; no retail code, assets, tickets or keys."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from app.tools.three_ds import DumpError, find_update, inspect_dump

BASE = 0x0004000000ABC000
UPDATE = 0x0004000E00ABC000


def ncch(title=BASE, *, decrypted=True):
    b = bytearray(0xC00)
    b[0x100:0x104] = b"NCCH"
    struct.pack_into("<I", b, 0x104, len(b) // 0x200)
    struct.pack_into("<Q", b, 0x118, title)
    struct.pack_into("<I", b, 0x180, 0x400)
    b[0x18F] = 4 if decrypted else 0
    b[0x200:0x600] = bytes(range(256)) * 4
    b[0x160:0x180] = hashlib.sha256(b[0x200:0x600]).digest()
    struct.pack_into("<III", b, 0x1A0, 5, 1, 1)
    b[0xA00:0xC00] = b"SYNTHETIC".ljust(0x200, b"\0")
    b[0x1C0:0x1E0] = hashlib.sha256(b[0xA00:0xC00]).digest()
    return b


def cci(**kwargs):
    part = ncch(**kwargs)
    b = bytearray(0x4000) + part
    b[0x100:0x104] = b"NCSD"
    struct.pack_into("<II", b, 0x120, 0x20, len(part) // 0x200)
    return b


def cia(title=UPDATE, version=0x1234, *, encrypted=False, decrypted=True):
    part = ncch(title, decrypted=decrypted)
    tmd = 0x2040
    body = tmd + 0x140
    tmd_size = 0x140 + 0x9C4 + 0x30
    data = (tmd + tmd_size + 63) & ~63
    b = bytearray(data) + part
    struct.pack_into("<IHHIIIIQ", b, 0, 0x2020, 0, 0, 0, 0, tmd_size, 0, len(part))
    b[0x20] = 0x80
    struct.pack_into(">I", b, tmd, 0x10004)
    struct.pack_into(">Q", b, body + 0x4C, title)
    struct.pack_into(">HH", b, body + 0x9C, version, 1)
    struct.pack_into(">IHHQ", b, body + 0x9C4, 0, 0, int(encrypted), len(part))
    return b


class Containers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, data):
        p = self.root / name
        p.write_bytes(data)
        return p

    def base(self):
        return inspect_dump(self.put("base.cci", cci()))

    def test_decrypted_cartridge_and_plaintext_hashes(self):
        info = self.base()
        self.assertEqual((info.kind, info.title_id, info.title_version), ("cci", BASE, None))
        self.assertTrue(info.decrypted)

    def test_encrypted_ncch_is_rejected(self):
        path = self.put("base.cci", cci(decrypted=False))
        with self.assertRaisesRegex(DumpError, "GodMode9"):
            inspect_dump(path)
        self.assertFalse(inspect_dump(path, require_decrypted=False).decrypted)

    def test_plaintext_flag_does_not_override_bad_hash(self):
        b = cci()
        b[0x4200] ^= 0xFF
        with self.assertRaisesRegex(DumpError, "plaintext hash mismatch"):
            inspect_dump(self.put("base.cci", b))

    def test_out_of_bounds_partition(self):
        b = cci()
        struct.pack_into("<I", b, 0x124, 0xFFFFFFFF)
        with self.assertRaisesRegex(DumpError, "exceeds"):
            inspect_dump(self.put("base.cci", b))

    def test_exefs_cannot_escape_its_ncch(self):
        b = ncch()
        struct.pack_into("<I", b, 0x1A0, 0x100)
        with self.assertRaisesRegex(DumpError, "ExeFS bounds"):
            inspect_dump(self.put("base.cxi", b))

    def test_cia_uses_tmd_version(self):
        info = inspect_dump(self.put("misleading-v9999.cia", cia(version=7)))
        self.assertEqual((info.title_id, info.title_version), (UPDATE, 7))
        self.assertTrue(info.decrypted)

    def test_encrypted_cia_layer_is_rejected(self):
        p = self.put("update.cia", cia(encrypted=True))
        with self.assertRaisesRegex(DumpError, "encrypted"):
            inspect_dump(p)
        self.assertEqual(inspect_dump(p, require_decrypted=False).title_id, UPDATE)

    def test_encrypted_inner_ncch_is_rejected(self):
        with self.assertRaisesRegex(DumpError, "encrypted"):
            inspect_dump(self.put("update.cia", cia(decrypted=False)))

    def test_auto_update_matches_title_and_highest_metadata_version(self):
        base = self.base()
        self.put("new-name.cia", cia(version=2))
        wanted = self.put("old-name.cia", cia(version=8))
        self.put("unrelated.cia", cia(title=UPDATE + 0x100, version=99))
        selected = find_update(base)
        assert selected is not None
        self.assertEqual(selected.path, wanted)

    def test_newest_encrypted_update_does_not_fall_back_to_old(self):
        base = self.base()
        self.put("old.cia", cia(version=2))
        self.put("new.cia", cia(version=8, encrypted=True))
        with self.assertRaisesRegex(DumpError, "encrypted"):
            find_update(base)

    def test_matching_truncated_update_is_not_silently_omitted(self):
        base = self.base()
        self.put("update.cia", cia()[:-0x200])
        with self.assertRaisesRegex(DumpError, "exceeds"):
            find_update(base)

    def test_conflicting_same_version_requires_explicit_selection(self):
        base = self.base()
        self.put("a.cia", cia())
        self.put("b.cia", cia())
        with self.assertRaisesRegex(DumpError, "same version"):
            find_update(base)

    def test_unrelated_unidentifiable_cia_is_not_an_update(self):
        base = self.base()
        self.put("unrelated.cia", b"not a CIA")
        self.assertIsNone(find_update(base))


if __name__ == "__main__":
    unittest.main()
