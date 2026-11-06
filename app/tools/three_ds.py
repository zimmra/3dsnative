"""Read 3DS container metadata without keys, extraction, or writes to the dump.

Offsets follow the NCSD, NCCH, CIA and title-metadata layouts documented by
3dbrew. A plaintext flag alone is insufficient: verify the main NCCH's
exheader and ExeFS superblock hashes before accepting a decrypted backup.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import struct
from typing import BinaryIO


class DumpError(ValueError):
    """A missing, malformed, encrypted, or inconsistent input."""


@dataclass(frozen=True)
class Content:
    index: int
    offset: int
    size: int
    encrypted: bool


@dataclass(frozen=True)
class NCCH:
    offset: int
    size: int
    program_id: int
    decrypted: bool
    product_code: str


@dataclass(frozen=True)
class Dump:
    path: Path
    kind: str
    title_id: int
    title_version: int | None
    contents: tuple[Content, ...]
    partitions: tuple[NCCH, ...]

    @property
    def decrypted(self) -> bool:
        return bool(self.partitions) and all(p.decrypted for p in self.partitions) and not any(
            c.encrypted for c in self.contents
        )


def _read(f: BinaryIO, offset: int, size: int, limit: int) -> bytes:
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise DumpError(f"Container range exceeds the file: offset={offset:#x}, size={size:#x}")
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise DumpError("The dump was truncated while reading it")
    return data


def _sha256(f: BinaryIO, offset: int, size: int, limit: int) -> bytes:
    h = hashlib.sha256()
    while size:
        n = min(size, 1024 * 1024)
        h.update(_read(f, offset, n, limit))
        offset += n
        size -= n
    return h.digest()


def _ncch(f: BinaryIO, offset: int, size: int, limit: int, verify: bool) -> NCCH:
    if size < 0x200 or offset > limit or size > limit - offset:
        raise DumpError("NCCH partition exceeds its container")
    h = _read(f, offset, 0x200, limit)
    if h[0x100:0x104] != b"NCCH":
        raise DumpError(f"Missing NCCH header at {offset:#x}")
    unit = 0x200 << h[0x18E]
    declared = struct.unpack_from("<I", h, 0x104)[0] * unit
    if declared < 0x200 or declared > size:
        raise DumpError("NCCH content size exceeds its partition")
    result = NCCH(offset, declared, struct.unpack_from("<Q", h, 0x118)[0],
                  bool(h[0x18F] & 4), h[0x150:0x160].rstrip(b"\0").decode("ascii", "replace"))
    if not result.decrypted or not verify:
        return result
    end = offset + declared
    exheader_size = struct.unpack_from("<I", h, 0x180)[0]
    if exheader_size:
        if exheader_size > 0x800:
            raise DumpError("Invalid NCCH extended-header size")
        if _sha256(f, offset + 0x200, exheader_size, end) != h[0x160:0x180]:
            raise DumpError("Extended-header plaintext hash mismatch; re-dump decrypted with GodMode9")
    ef_off, ef_size, hash_size = struct.unpack_from("<III", h, 0x1A0)
    if ef_size:
        if hash_size == 0 or hash_size > ef_size or ef_off * unit > declared or ef_size * unit > declared - ef_off * unit:
            raise DumpError("Invalid NCCH ExeFS bounds")
        if _sha256(f, offset + ef_off * unit, hash_size * unit, end) != h[0x1C0:0x1E0]:
            raise DumpError("ExeFS plaintext hash mismatch; re-dump decrypted with GodMode9")
    return result


def _align64(n: int) -> int:
    return (n + 63) & ~63


def _cia(f: BinaryIO, limit: int, *, metadata_only: bool = False) -> tuple[int, int, tuple[Content, ...]]:
    h = _read(f, 0, 0x20, limit)
    header, _, _, cert, ticket, tmd_size, _, content_size = struct.unpack("<IHHIIIIQ", h)
    if header < 0x2020:
        raise DumpError("Invalid CIA header size")
    tmd = _align64(_align64(_align64(header) + cert) + ticket)
    data = _align64(tmd + tmd_size)
    signatures = {0x10000: 0x240, 0x10001: 0x140, 0x10002: 0x80,
                  0x10003: 0x240, 0x10004: 0x140, 0x10005: 0x80}
    tmd_end = min(tmd + tmd_size, limit)
    signature = struct.unpack(">I", _read(f, tmd, 4, tmd_end))[0]
    if signature not in signatures:
        raise DumpError("Unknown CIA TMD signature format")
    body = tmd + signatures[signature]
    b = _read(f, body, 0xC4, tmd_end)
    title_id = struct.unpack_from(">Q", b, 0x4C)[0]
    version, count = struct.unpack_from(">HH", b, 0x9C)
    if metadata_only:
        return title_id, version, ()
    if data > limit or content_size > limit - data:
        raise DumpError("CIA content section exceeds its file")
    bitmap = _read(f, 0x20, 0x2000, limit)
    records = _read(f, body + 0x9C4, count * 0x30, tmd_end)
    offset = data
    contents = []
    indices = set()
    for i in range(count):
        _, index, flags, size = struct.unpack_from(">IHHQ", records, i * 0x30)
        if index in indices:
            raise DumpError("CIA TMD contains duplicate content indices")
        indices.add(index)
        if not bitmap[index // 8] & (0x80 >> (index % 8)):
            continue
        if size > data + content_size - offset:
            raise DumpError("CIA content record exceeds its section")
        contents.append(Content(index, offset, size, bool(flags & 1)))
        offset += size
    if offset != data + content_size:
        raise DumpError("CIA content records do not match the content-section size")
    if not any(c.index == 0 for c in contents):
        raise DumpError("CIA has no included main content")
    return title_id, version, tuple(contents)


def inspect_dump(path: str | Path, *, verify_hashes: bool = True,
                 require_decrypted: bool = True) -> Dump:
    path = Path(path).expanduser().resolve()
    try:
        with path.open("rb") as f:
            limit = os.fstat(f.fileno()).st_size
            h = _read(f, 0, 0x200, limit)
            partitions = []
            contents: tuple[Content, ...] = ()
            version = None
            if h[0x100:0x104] == b"NCSD":
                kind = "cci"
                for i in range(8):
                    start, size = struct.unpack_from("<II", h, 0x120 + i * 8)
                    if i == 0 and not size:
                        raise DumpError("Cartridge image has no main partition")
                    if size:
                        partitions.append(_ncch(f, start * 0x200, size * 0x200, limit, verify_hashes))
                title_id = partitions[0].program_id
            elif h[0x100:0x104] == b"NCCH":
                kind = "cxi"
                partitions.append(_ncch(f, 0, limit, limit, verify_hashes))
                title_id = partitions[0].program_id
            else:
                kind = "cia"
                title_id, version, contents = _cia(f, limit)
                for c in contents:
                    if c.encrypted:
                        continue
                    p = _ncch(f, c.offset, c.size, limit, verify_hashes)
                    if c.index == 0 and p.program_id != title_id:
                        raise DumpError("CIA main content and TMD disagree on the title ID")
                    partitions.append(p)
            result = Dump(path, kind, title_id, version, contents, tuple(partitions))
            if require_decrypted and not result.decrypted:
                raise DumpError(f"{path.name} is encrypted; re-dump decrypted with GodMode9")
            return result
    except OSError as e:
        raise DumpError(f"Cannot read {path}: {e.strerror}") from e


def find_update(base: Dump, folder: Path | None = None) -> Dump | None:
    """Choose by TMD title ID/version, then strictly validate the selected CIA."""
    if base.title_id >> 32 != 0x00040000:
        raise DumpError("The base input is not a 3DS application title")
    expected = base.title_id | 0x0000000E00000000
    candidates = []
    for path in (folder or base.path.parent).iterdir():
        if path.suffix.lower() != ".cia" or not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                title, version, _ = _cia(f, os.fstat(f.fileno()).st_size, metadata_only=True)
        except (OSError, DumpError):
            continue  # An unrelated or unidentifiable CIA cannot establish a title match.
        if title == expected:
            candidates.append((version, path))
    if not candidates:
        return None
    newest = max(v for v, _ in candidates)
    selected = [p for v, p in candidates if v == newest]
    if len(selected) != 1:
        raise DumpError("Several matching updates have the same version; specify --update explicitly")
    return inspect_dump(selected[0])
