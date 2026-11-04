"""Decode a backup's SMDH icon locally; no game artwork enters the repository."""
from pathlib import Path
import struct
import zlib
from three_ds import Dump, DumpError


def read_smdh(dump: Dump) -> bytes:
    partition = next(p for p in dump.partitions if p.program_id == dump.title_id)
    with dump.path.open('rb') as source:
        source.seek(partition.offset)
        header = source.read(0x200)
        unit = 0x200 << header[0x18e]
        offset, size = struct.unpack_from('<II', header, 0x1a0)
        if not size:
            return b''
        exefs = partition.offset + offset * unit
        source.seek(exefs)
        entries = source.read(0x200)
        for i in range(10):
            name, start, length = struct.unpack_from('<8sII', entries, i * 16)
            if name.rstrip(b'\0') == b'icon' and 0x36c0 <= length <= size * unit - 0x200 - start:
                source.seek(exefs + 0x200 + start)
                data = source.read(0x36c0)
                if data[:4] == b'SMDH':
                    return data
    return b''


def icon_rgb(smdh: bytes) -> bytes:
    if len(smdh) < 0x36c0 or smdh[:4] != b'SMDH':
        raise DumpError('The backup has no valid SMDH icon')
    output = bytearray()
    for y in range(48):
        for x in range(48):
            morton = sum(((x >> b) & 1) << (2*b) | ((y >> b) & 1) << (2*b+1) for b in range(3))
            offset = 0x24c0 + 2 * (((y//8)*6 + x//8)*64 + morton)
            value = struct.unpack_from('<H', smdh, offset)[0]
            r, g, b = value >> 11, value >> 5 & 63, value & 31
            output.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return bytes(output)


def png(rgb: bytes, size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            offset = ((y*48//size)*48 + x*48//size)*3
            rows.extend(rgb[offset:offset+3])
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size,size,8,2,0,0,0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def install_icon(dump: Dump, app: Path, info: dict) -> None:
    rgb = icon_rgb(read_smdh(dump))
    for scale in (2, 3):
        (app / f'AppIcon60x60@{scale}x.png').write_bytes(png(rgb, 60*scale))
    info['CFBundleIcons'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles':['AppIcon60x60'], 'UIPrerenderedIcon':False}}
