#!/usr/bin/env python3
"""Pack a decrypted personal 3DS backup and matching update into an unsigned IPA.

Uses the pinned, interpreter-only Cytrus runtime. No JIT permission, signing,
Nintendo keys, generated game code, or remote game upload is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "app/tools"))
from three_ds import Dump, DumpError, find_update, inspect_dump  # noqa: E402
from game_icon import install_icon
from duo_shell import verify as verify_duo_shell


def outside_repo(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path == Path("/Volumes/ZNAS") or Path("/Volumes/ZNAS") in path.parents:
        raise DumpError("The NAS is read-only; choose a local work/output directory")
    path = path.resolve()
    if path == REPO or REPO in path.parents:
        raise DumpError("Private work and output paths must be outside the repository")
    if path == Path("/Volumes/ZNAS") or Path("/Volumes/ZNAS") in path.parents:
        raise DumpError("The NAS is read-only; choose a local work/output directory")
    return path


def check_nas(path: Path) -> None:
    if str(path).startswith("/Volumes/ZNAS/") and not os.path.ismount("/Volumes/ZNAS"):
        raise DumpError("Mount /Volumes/ZNAS before reading the backup")


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_input(dump: Dump, destination: Path) -> str:
    source = dump.path.resolve()
    check_nas(source)
    stat = source.stat()
    identity = {"source": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    stamp = destination.with_suffix(destination.suffix + ".json")
    previous = json.loads(stamp.read_text()) if stamp.exists() else {}
    if destination.exists() and all(previous.get(k) == v for k, v in identity.items()):
        digest = fingerprint(destination)
        if digest == previous.get("sha256"):
            return digest
    temporary = destination.with_suffix(destination.suffix + ".partial")
    # Copy bytes only: SMB file flags and metadata must not propagate locally.
    shutil.copyfile(source, temporary)
    if (source.stat().st_size, source.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        temporary.unlink()
        raise DumpError("Backup changed during copying; retry after the transfer finishes")
    digest = fingerprint(temporary)
    copied = inspect_dump(temporary)
    if not copied.decrypted or copied.title_id != dump.title_id:
        raise DumpError("Cached backup failed validation")
    temporary.replace(destination)
    stamp.write_text(json.dumps({**identity, "sha256": digest}, indent=2) + "\n")
    return digest


def title_name(dump: Dump) -> str:
    """Read the English short title from a decrypted ExeFS icon, if present."""
    partition = next(p for p in dump.partitions if p.program_id == dump.title_id)
    with dump.path.open("rb") as source:
        source.seek(partition.offset)
        header = source.read(0x200)
        unit = 0x200 << header[0x18E]
        offset, size = struct.unpack_from("<II", header, 0x1A0)
        if size:
            exefs = partition.offset + offset * unit
            source.seek(exefs)
            entries = source.read(0x200)
            for i in range(10):
                name, start, length = struct.unpack_from("<8sII", entries, i * 16)
                if name.rstrip(b"\0") == b"icon" and length >= 0x288 and start + length <= size * unit - 0x200:
                    source.seek(exefs + 0x200 + start)
                    icon = source.read(0x288)
                    if icon[:4] == b"SMDH":
                        title = icon[0x208:0x288].decode("utf-16-le").split("\0", 1)[0].strip()
                        if title:
                            return title
    return f"3DS {dump.title_id:016X}"


def ipa_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", ascii_name) or "3DSGame"


def pack(app: Path, destination: Path) -> None:
    info = plistlib.loads((app / "Info.plist").read_bytes())
    executable = app / info["CFBundleExecutable"]
    signature = subprocess.run(["codesign", "-dv", str(executable)], capture_output=True, text=True)
    if signature.returncode == 0:
        raise DumpError("The app executable is signed; the packer only emits unsigned builds")
    if (app / "embedded.mobileprovision").exists():
        raise DumpError("Unexpected provisioning profile in unsigned build")
    temporary = destination.with_suffix(".ipa.partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for path in sorted(app.rglob("*")):
            relative = path.relative_to(app)
            if "_CodeSignature" in relative.parts:
                continue
            if path.is_symlink():
                raise DumpError(f"Unexpected symlink in iOS app: {relative}")
            if path.is_file():
                archive.write(path, str(Path("Payload") / app.name / relative))
    temporary.replace(destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path, help="Decrypted .3ds/.cci backup")
    parser.add_argument("--update", type=Path, help="Matching decrypted CIA; otherwise detected alongside the game")
    parser.add_argument("--work", type=Path, default=Path.home() / "Development/3ds-work")
    parser.add_argument("--out", type=Path, help="Local output directory (defaults to --work)")
    parser.add_argument("--name", help="Override the English title read from the backup")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--duo-shell", type=Path, help="Verified game-free iOS 27.1-built 3dsnative.app")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work = outside_repo(args.work)
    out = outside_repo(args.out or work)
    # This Intel Mac's Homebrew prefix is /usr/local/Homebrew. Keep a plain
    # terminal invocation usable without requiring a particular shell profile.
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), "/usr/local/Homebrew/bin", "/usr/local/bin", "/opt/homebrew/bin"])
    for tool in ("git", "python3", "xcodegen", "xcodebuild", "codesign"):
        if not shutil.which(tool):
            raise DumpError(f"Missing prerequisite: {tool}")
    if args.jobs < 1:
        raise DumpError("--jobs must be positive")
    check_nas(args.game.expanduser().absolute())
    base = inspect_dump(args.game.expanduser())
    if base.kind != "cci" or base.title_id >> 32 != 0x00040000 or not base.decrypted:
        raise DumpError("Provide a decrypted 3DS application .3ds/.cci backup")
    if args.update:
        check_nas(args.update.expanduser().absolute())
    update = inspect_dump(args.update.expanduser()) if args.update else find_update(base)
    if update and (not update.decrypted or update.title_id != base.title_id | 0x0000000E00000000):
        raise DumpError("Update must be decrypted and match the base title ID")
    name = args.name or title_name(base)
    private = work / "titles" / f"{base.title_id:016X}" / "Game"
    private.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[1/3] Validated {name}: {base.title_id:016X}", flush=True)
    base_hash = cache_input(base, private / "base.cci")
    manifest = {"name": name, "title_id": f"{base.title_id:016X}", "game": "base.cci", "game_sha256": base_hash}
    if update:
        print(f"Update {update.title_id:016X}, TMD version {update.title_version}; installed locally at first launch", flush=True)
        manifest.update(update="update.cia", update_sha256=cache_input(update, private / "update.cia"))
    else:
        (private / "update.cia").unlink(missing_ok=True)
    (private / "game.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for child in ("tmp", "cache", "cache/pycache", "cache/swiftpm-modules"):
        (work / child).mkdir(parents=True, exist_ok=True)
    os.environ.update(TMPDIR=str(work / "tmp") + "/", XDG_CACHE_HOME=str(work / "cache"),
                      PYTHONPYCACHEPREFIX=str(work / "cache/pycache"),
                      SWIFTPM_MODULECACHE_OVERRIDE=str(work / "cache/swiftpm-modules"))
    shell = args.duo_shell or work / "native-duo-shell/3dsnative.app"
    if shell.exists():
        verify_duo_shell(shell)
        print("[2/3] Reusing verified native Duo executable; adding private game files locally", flush=True)
        app = work / "builds" / f"packed-{base.title_id:016X}" / "3dsnative.app"
        if app.exists():
            shutil.rmtree(app)
        shutil.copytree(shell, app)
        shutil.copytree(private, app / "Game")
    elif args.duo_shell:
        raise DumpError("The requested native Duo shell does not exist")
    else:
        print("[2/3] Building the local SDK 26 fallback (no native Duo mode)", flush=True)
        subprocess.run([sys.executable, str(REPO / "app/tools/build_cytrus.py"), "--sdk", "iphoneos",
                        "--work", str(work), "--jobs", str(args.jobs), "--game-resources", str(private)], check=True)
        app = work / "builds/cytrus-device/Build/Products/Release-iphoneos/3dsnative.app"
    info = plistlib.loads((app / "Info.plist").read_bytes())
    info["CFBundleIdentifier"] = f"org.threedsnative.title{base.title_id:016x}"
    info["CFBundleDisplayName"] = name
    install_icon(base, app, info)
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    destination = out / (ipa_name(name) + ".ipa")
    print("[3/3] Packing unsigned IPA", flush=True)
    pack(app, destination)
    print(f"Unsigned IPA: {destination}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (DumpError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
