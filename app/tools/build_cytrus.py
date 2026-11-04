#!/usr/bin/env python3
"""Build a game-independent Cytrus static-library probe on this Mac.

This is a development tool, not the IPA packer. Downloads, patched public
dependencies, projects and compiler output stay under --work. No game is read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


PINS = {
    "Folium": ("https://github.com/folium-app/Folium", "4dbc9e8e8450eef350109fe0e816554dcfaee7e5"),
    "SharedDependencies": ("https://github.com/folium-app/SharedDependencies", "9787aa9062d8ff220f127052597e6f3ae1dcbc7a"),
    "Vulkan-Headers": ("https://github.com/KhronosGroup/Vulkan-Headers", "6802bb4733b63ed5efd3adb308a6c885ef180ea1"),
    "VulkanMemoryAllocator": ("https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator", "1d8f600fd424278486eade7ed3e877c99f0846b1"),
}
CYTRUS_REVISION = "d421219acdcb0ad5494ab68777a52a87465fd8ae"
ZSTD_REVISION = "9baae4431585740983318801beff0a7ae4494291"
OPENSSL_REVISION = "0a6149bff4fb00921ce0ec1811f726b166638095"
PLZMA_REVISION = "321f199010d09d956bd27ba1454026801976a898"


def run(args: list[str], *, cwd: Path | None = None) -> str:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    return result.stdout.strip()


def checkout(work: Path, name: str) -> Path:
    url, revision = PINS[name]
    dest = work / "upstream" / name
    if not dest.exists():
        dest.mkdir(parents=True)
        run(["git", "init", str(dest)])
        run(["git", "remote", "add", "origin", url], cwd=dest)
        run(["git", "fetch", "--depth=1", "origin", revision], cwd=dest)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=dest)
    if run(["git", "rev-parse", "HEAD"], cwd=dest) != revision:
        raise RuntimeError(f"Unexpected revision in {dest}; preserve it and choose a different --work")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=dest):
        raise RuntimeError(f"Public source checkout is modified: {dest}")
    return dest


def patch_dependencies(source: Path, target: Path) -> None:
    """Use a separate writable copy; keep fetched source and provenance intact."""
    original = (source / "Package.swift").read_text()
    needle = '.define("CRYPTOPP_DISABLE_ARM_CRC32"),'
    replacement = needle + '''
            // These Crypto++ kernels use runtime CPU capability dispatch.
            // Their intrinsic implementations require explicit x86 ISA flags.
            .unsafeFlags(["-Xarch_x86_64", "-maes", "-Xarch_x86_64", "-msha",
                          "-Xarch_x86_64", "-mpclmul", "-Xarch_x86_64", "-msse4.1"]),'''
    if original.count(needle) != 1:
        raise RuntimeError("Upstream Crypto++ target changed; review its compile flags")
    patched = original.replace(needle, replacement)
    for url, branch, revision in (
        ("https://github.com/jarrodnorwell/PLzmaSDK", "master", PLZMA_REVISION),
        ("https://github.com/krzyzanowskim/OpenSSL", "main", OPENSSL_REVISION),
    ):
        needle = f'.package(url: "{url}", branch: "{branch}")'
        if patched.count(needle) != 1:
            raise RuntimeError(f"Upstream package declaration changed: {url}")
        patched = patched.replace(needle, f'.package(url: "{url}", revision: "{revision}")')
    if not target.exists():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", ".build", ".swiftpm"))
    manifest = target / "Package.swift"
    if manifest.read_text() != patched:
        manifest.chmod(0o644)
        manifest.write_text(patched)
    # SwiftPM has target-wide flags, whereas Crypto++ normally builds its SIMD
    # translation units with per-file flags. Preserve the scalar dispatcher's
    # baseline ISA by marking only these implementations with Clang's target
    # attribute; arm64 preprocessing is unchanged.
    kernels = {"chacha_avx.cpp": "avx2", "lsh256_avx.cpp": "avx2",
               "lsh512_avx.cpp": "avx2", "crc_simd.cpp": "sse4.2"}
    for filename, features in kernels.items():
        original = (source / "Sources/cryptopp" / filename).read_text()
        marker = "ANONYMOUS_NAMESPACE_BEGIN" if "ANONYMOUS_NAMESPACE_BEGIN" in original else "NAMESPACE_BEGIN(CryptoPP)"
        if marker not in original:
            raise RuntimeError(f"Review the SIMD implementation layout: {filename}")
        pragma = ('#if defined(__clang__) && defined(__x86_64__)\n'
                  f'#pragma clang attribute push (__attribute__((target("{features}"))), apply_to=function)\n'
                  '#endif\n\n')
        patched_source = original.replace(marker, pragma + marker, 1)
        patched_source += ('\n#if defined(__clang__) && defined(__x86_64__)\n'
                           '#pragma clang attribute pop\n#endif\n')
        dest = target / "Sources/cryptopp" / filename
        if dest.read_text() != patched_source:
            dest.chmod(0o644)
            dest.write_text(patched_source)


def project(work: Path, folium: Path, shared: Path) -> dict:
    definitions = ("CPPHTTPLIB_OPENSSL_SUPPORT FOR_CYTRUS HAVE_FASTINTERP HAVE_COREAUDIO "
                   "HAVE_OPENAL HAVE_SDL3 ENABLE_GDBSTUB ENABLE_SDL3 ENABLE_VULKAN ENABLE_WEB_SERVICE")
    return {
        "name": "CytrusCoreProbe",
        "options": {"deploymentTarget": {"iOS": "17.0"}},
        "packages": {
            "SharedDependencies": {"path": str(shared)},
            "Zstd": {"url": "https://github.com/jarrodnorwell/zstd", "revision": ZSTD_REVISION},
        },
        "targets": {"CytrusCore": {
            "type": "library.static", "platform": "iOS",
            "sources": [{"path": str(folium / "Cytrus/System"), "excludes": ["include"]}],
            "dependencies": [
                {"package": "SharedDependencies", "product": "SharedDependencies"},
                {"package": "Zstd", "products": ["libseekable_format", "libzstd", "libzstdwrapper"]},
            ],
            "settings": {"base": {
                "CODE_SIGNING_ALLOWED": "NO",
                "CLANG_CXX_LANGUAGE_STANDARD": "gnu++23", "GCC_C_LANGUAGE_STANDARD": "gnu23",
                "GCC_PREPROCESSOR_DEFINITIONS": "$(inherited) " + definitions,
                "HEADER_SEARCH_PATHS": ["$(inherited)", str(folium / "Cytrus/System/include"),
                                        str(work / "upstream/Vulkan-Headers/include"),
                                        str(work / "upstream/VulkanMemoryAllocator/include")],
                "OTHER_CPLUSPLUSFLAGS": "$(inherited) -Wno-shorten-64-to-32",
            }},
        }},
        "schemes": {"CytrusCore": {"build": {"targets": {"CytrusCore": "all"}}}},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path.home() / "Development/3ds-work")
    parser.add_argument("--sdk", choices=["iphoneos", "iphonesimulator"], default="iphonesimulator")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    work = args.work.expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if work == repo or repo in work.parents:
        parser.error("--work must be outside the repository")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for tool in ("git", "xcodegen", "xcodebuild"):
        if not shutil.which(tool):
            parser.error(f"Missing prerequisite: {tool}")
    # Keep SwiftPM, compiler and temporary files with the work products.
    for child in ("tmp", "cache/swiftpm", "cache/cytrus-spm", "logs", "cytrus-build-source"):
        (work / child).mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(work / "tmp") + "/"
    folium = checkout(work, "Folium")
    run(["git", "submodule", "update", "--init", "--depth=1", "Cytrus"], cwd=folium)
    if run(["git", "rev-parse", "HEAD"], cwd=folium / "Cytrus") != CYTRUS_REVISION:
        raise RuntimeError("Cytrus revision differs from the reviewed core")
    source = checkout(work, "SharedDependencies")
    checkout(work, "Vulkan-Headers")
    checkout(work, "VulkanMemoryAllocator")
    shared = work / "public-deps/SharedDependencies"
    patch_dependencies(source, shared)
    build_source = work / "cytrus-build-source"
    spec = build_source / "project.json"
    spec.write_text(json.dumps(project(work, folium, shared), indent=2) + "\n")
    run(["xcodegen", "generate", "--spec", str(spec), "--project", str(build_source)])
    if args.prepare_only:
        return
    arch = "arm64" if args.sdk == "iphoneos" else "x86_64"
    platform = "device" if args.sdk == "iphoneos" else "simulator"
    log = work / f"logs/cytrus-build-{args.sdk}.log"
    command = ["xcodebuild", "-project", str(build_source / "CytrusCoreProbe.xcodeproj"),
               "-scheme", "CytrusCore", "-configuration", "Release", "-sdk", args.sdk,
               "-jobs", str(args.jobs), "-derivedDataPath", str(work / f"builds/cytrus-{platform}"),
               "-clonedSourcePackagesDirPath", str(work / "cache/cytrus-spm"),
               "-packageCachePath", str(work / "cache/swiftpm"),
               "-packageAuthorizationProvider", "netrc", f"ARCHS={arch}",
               "ONLY_ACTIVE_ARCH=NO", "CODE_SIGNING_ALLOWED=NO", "build"]
    print(f"Building {arch}; compiler output: {log}", flush=True)
    with log.open("w") as output:
        subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    main()
