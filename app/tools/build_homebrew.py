#!/usr/bin/env python3
"""Build the original ARM11 smoke fixture as a relocation-free .3dsx.

Needs Clang and LLVM objcopy. No Nintendo SDK, keys or game data are used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import struct
import subprocess


def build(output: Path, clang: str, objcopy: str) -> None:
    source = Path(__file__).resolve().parents[2] / "tests/homebrew/smoke.S"
    output.parent.mkdir(parents=True, exist_ok=True)
    obj = output.with_suffix(".o")
    text = output.with_suffix(".text.bin")
    subprocess.run([clang, "--target=armv6k-none-eabi", "-c", str(source), "-o", str(obj)], check=True)
    subprocess.run([objcopy, "-O", "binary", "--only-section=.text", str(obj), str(text)], check=True)
    code = text.read_bytes()
    # Extended 3DSX header, three empty relocation tables, one code segment.
    # The fixture uses only PC-relative references within that segment.
    header = struct.pack("<4sHH9I", b"3DSX", 44, 8, 0, 0, len(code), 0, 0, 0, 0, 0, 0)
    output.write_bytes(header + bytes(3 * 8) + code)
    print(f"Built original ARM11 smoke fixture: {output} ({output.stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path.home() / "Development/3ds-work/homebrew/smoke.3dsx")
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--objcopy", default="llvm-objcopy")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = args.out.expanduser().resolve()
    if output == repo or repo in output.parents:
        parser.error("Generated homebrew output must be outside the repository")
    for tool in (args.clang, args.objcopy):
        if not shutil.which(tool):
            parser.error(f"Missing prerequisite: {tool}")
    build(output, args.clang, args.objcopy)


if __name__ == "__main__":
    main()
