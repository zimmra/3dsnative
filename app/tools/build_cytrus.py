#!/usr/bin/env python3
"""Build the pinned Cytrus runtime and optional unsigned iOS host on this Mac.

This is a development tool, called by the IPA packer. Downloads, patched public
dependencies, projects and compiler output stay under --work. The default builds
only public runtime code; --game-resources explicitly adds private game files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
from cytrus_patches import simulator_texel_buffers


PINS = {
    "Folium": ("https://github.com/folium-app/Folium", "4dbc9e8e8450eef350109fe0e816554dcfaee7e5"),
    "SharedDependencies": ("https://github.com/folium-app/SharedDependencies", "9787aa9062d8ff220f127052597e6f3ae1dcbc7a"),
    "Vulkan-Headers": ("https://github.com/KhronosGroup/Vulkan-Headers", "6802bb4733b63ed5efd3adb308a6c885ef180ea1"),
    "VulkanMemoryAllocator": ("https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator", "1d8f600fd424278486eade7ed3e877c99f0846b1"),
    "SPIRV-Headers": ("https://github.com/KhronosGroup/SPIRV-Headers", "496543121ce6419f23d6fa5d7194ba66c36212d2"),
    "BoostSerialization": ("https://github.com/boostorg/serialization", "91c265be640294fbac4190b5084f9570f06f72ba"),
    "BoostIostreams": ("https://github.com/boostorg/iostreams", "dacdb3db57e9f372ad575f02a45b5efb439d8889"),
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
    # This iOS target never instantiates the native-code CPU JIT.
    patched = patched.replace('            "lib_dynarmic",\n', "")
    # CHD support belongs to other console cores, not the 3DS runtime. It also
    # pulls an unrelated Swift PLzma wrapper into the static archive.
    patched = patched.replace('            "libchdr",\n', "")
    # Dynamic frameworks cannot be folded into a static archive. Expose SDL's
    # binary product separately so the final app links and embeds it once.
    patched = patched.replace("    products: [", '    products: [\n        .library(name: "CytrusSDL3", targets: ["lib_sdl3"]),', 1)
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


def prepare_core(source: Path, target: Path) -> Path:
    if not target.exists():
        shutil.copytree(source, target)
    # <string> and <span> do not promise to include every type trait. Do not
    # depend on transitive declarations that Xcode 26.3's libc++ omits.
    header = source / "include/common/hash.h"
    patched = header.read_text().replace("#include <string>", "#include <string>\n#include <type_traits>")
    dest = target / "include/common/hash.h"
    if dest.read_text() != patched:
        dest.chmod(0o644)
        dest.write_text(patched)
    guard = "#if CITRA_ARCH(x86_64) || CITRA_ARCH(arm64)"
    for relative in ("core/core.cpp", "core/arm/exclusive_monitor.cpp", "video_core/shader/shader.cpp"):
        original = (source / relative).read_text()
        if guard not in original:
            raise RuntimeError(f"Review upstream JIT selection: {relative}")
        patched = original.replace(guard, "#if !defined(THREEDS_NO_JIT) && (CITRA_ARCH(x86_64) || CITRA_ARCH(arm64))")
        if relative == "core/core.cpp":
            needle = "    memory = std::make_unique<Memory::MemorySystem>(*this);"
            patched = patched.replace(needle, """#ifdef THREEDS_NO_JIT
    // iOS cannot execute generated CPU code. Enforce this even if an imported
    // config requests JIT; GPU driver shader compilation is independent.
    Settings::values.use_cpu_jit.SetValue(false);
    Settings::values.use_shader_jit.SetValue(false);
#endif
""" + needle)
        dest = target / relative
        if dest.read_text() != patched:
            dest.chmod(0o644)
            dest.write_text(patched)
    header = source / "include/cytrus/bridge.h"
    patched = header.read_text().replace("void* context;", "extern void* context;")
    patched = patched.replace("LEDStatusChangedCallback callback;", "extern LEDStatusChangedCallback callback;")
    patched += "\nnamespace cytrus { void resize_screens(double, double, double, double); }\n"
    dest = target / "include/cytrus/bridge.h"
    if dest.read_text() != patched:
        dest.write_text(patched)
    bridge = (source / "bridge.cpp").read_text()
    bridge = bridge.replace("namespace AppleUtils {", "namespace cytrus { void* context{}; LEDStatusChangedCallback callback{}; }\n\nnamespace AppleUtils {", 1)
    bridge = bridge.replace("Common::Log::Initialize();", 'Common::Log::Initialize("log.txt");', 1)
    bridge = bridge.replace("        cytrus::callback(cytrus::context, color.x, color.y, color.z);",
                            "        if (cytrus::callback) cytrus::callback(cytrus::context, color.x, color.y, color.z);")
    bridge += """
// The host calls this on the emulation thread, between RunLoop calls. Frame
// preparation and touch mapping therefore observe the same window dimensions.
void cytrus::resize_screens(double top_h, double top_w, double bottom_h, double bottom_w) {
    cntnr.height = top_h; cntnr.width = top_w;
    cntnr.secondary_height = bottom_h; cntnr.secondary_width = bottom_w;
    if (cntnr.window) {
        cntnr.window->SizeChanged(top_h, top_w);
    }
    if (cntnr.secondary_window) {
        cntnr.secondary_window->SizeChanged(bottom_h, bottom_w);
    }
}
"""
    dest = target / "bridge.cpp"
    if dest.read_text() != bridge:
        dest.write_text(bridge)
    window = (source / "emu_window_vk.cpp").read_text()
    start = window.index("void EmulationWindow_Vulkan::SizeChanged(")
    end = window.index("\n}", start) + 2
    body = window[start:end]
    body = body.replace("    window_width = m_size_w;", "    window_width = m_size_w;\n    OnFramebufferSizeChanged();")
    window = window[:start] + body + window[end:]
    dest = target / "emu_window_vk.cpp"
    if dest.read_text() != window:
        dest.write_text(window)
    simulator_texel_buffers(source, target)
    return target


def project(work: Path, core: Path, shared: Path, smoke: bool = False, game_resources: Path | None = None) -> dict:
    definitions = ("THREEDS_NO_JIT CPPHTTPLIB_OPENSSL_SUPPORT FOR_CYTRUS HAVE_FASTINTERP HAVE_COREAUDIO "
                   "HAVE_OPENAL HAVE_SDL3 ENABLE_GDBSTUB ENABLE_SDL3 ENABLE_VULKAN ENABLE_WEB_SERVICE")
    spec = {
        "name": "CytrusCoreProbe",
        "options": {"deploymentTarget": {"iOS": "17.0"}},
        "packages": {
            "SharedDependencies": {"path": str(shared)},
            "Zstd": {"url": "https://github.com/jarrodnorwell/zstd", "revision": ZSTD_REVISION},
            "OpenSSL": {"url": "https://github.com/krzyzanowskim/OpenSSL", "revision": OPENSSL_REVISION},
        },
        "targets": {"CytrusCore": {
            "type": "library.static", "platform": "iOS",
            # Keep headers in the project: upstream uses basename includes that
            # rely on Xcode's target header map as well as rooted includes.
            "sources": [{"path": str(core), "headerVisibility": "project", "excludes": [
                "video_core/shader/shader_jit*.cpp", "core/arm/dynarmic/arm_dynarmic*.cpp",
                "core/arm/dynarmic/arm_exclusive_monitor.cpp",
                # Optional host video recording, not guest audio decoding.
                # The packer does not expose a video-dumper frontend.
                "core/dumping/ffmpeg_backend.cpp", "common/dynamic_library/ffmpeg.cpp",
                "audio_core/coreaudio_sink.cpp",
                "video_core/video_core.cpp",
            ]}, {
                # This flag gates only the renderer factory; the software
                # renderer sources are otherwise compiled unconditionally.
                "path": str(core / "video_core/video_core.cpp"),
                "compilerFlags": ["-DENABLE_SOFTWARE_RENDERER"],
            }, {"path": str(Path(__file__).resolve().parents[1] / "host/coreaudio_sink.cpp")}],
            "dependencies": [
                {"package": "SharedDependencies", "product": "SharedDependencies", "link": True},
                {"package": "Zstd", "products": ["libseekable_format", "libzstd", "libzstdwrapper"], "link": True},
                {"target": "BoostSerialization", "link": True},
                {"target": "BoostFileDescriptor", "link": True},
            ],
            "preBuildScripts": [{
                "name": "Restore glslang public header prefix",
                # The pinned XCFramework exports Include/ and Public/ at its
                # root, but SPIRV headers include glslang/Include/*.h. Alias the
                # package headers rather than substituting another SDK version.
                "script": 'mkdir -p "$DERIVED_FILE_DIR/upstream-headers"\n'
                          'ln -sfn "$BUILT_PRODUCTS_DIR/include" "$DERIVED_FILE_DIR/upstream-headers/glslang"',
                "outputFiles": ["$(DERIVED_FILE_DIR)/upstream-headers/glslang"],
                "showEnvVars": False,
            }],
            "settings": {"base": {
                "CODE_SIGNING_ALLOWED": "NO",
                "CLANG_CXX_LANGUAGE_STANDARD": "gnu++23", "GCC_C_LANGUAGE_STANDARD": "gnu23",
                "GCC_PREPROCESSOR_DEFINITIONS": "$(inherited) " + definitions,
                "HEADER_SEARCH_PATHS": ["$(inherited)", str(core / "include"),
                                        "$(DERIVED_FILE_DIR)/upstream-headers",
                                        str(work / "upstream/Vulkan-Headers/include"),
                                        str(work / "upstream/SPIRV-Headers/include"),
                                        str(work / "upstream/VulkanMemoryAllocator/include")],
                "OTHER_CPLUSPLUSFLAGS": "$(inherited) -Wno-shorten-64-to-32",
            }},
        }},
        "schemes": {"CytrusCore": {"build": {"targets": {"CytrusCore": "all"}}}},
    }
    # Folium's bundled Boost binaries omit x86_64 Simulator. Compile the exact
    # matching public sources on both architectures, avoiding an ABI split.
    for name, source in (("BoostSerialization", work / "upstream/BoostSerialization/src"),
                         ("BoostFileDescriptor", work / "upstream/BoostIostreams/src/file_descriptor.cpp")):
        spec["targets"][name] = {
            "type": "library.static", "platform": "iOS", "sources": [str(source)],
            "settings": {"base": {
                "CODE_SIGNING_ALLOWED": "NO", "CLANG_CXX_LANGUAGE_STANDARD": "gnu++23",
                "HEADER_SEARCH_PATHS": [str(shared / "Sources/boost/include")],
            }},
        }
    spec["targets"]["N3DSRuntime"] = {
        "type": "library.static", "platform": "iOS",
        "sources": [str(Path(__file__).resolve().parents[1] / "host/runtime.mm")],
        "dependencies": [{"target": "CytrusCore", "link": True}],
        "settings": {"base": {
            "CODE_SIGNING_ALLOWED": "NO", "CLANG_CXX_LANGUAGE_STANDARD": "gnu++23",
            "HEADER_SEARCH_PATHS": [str(core / "include")],
            "GCC_PREPROCESSOR_DEFINITIONS": "$(inherited) " + definitions,
        }},
    }
    spec["schemes"]["N3DSRuntime"] = {"build": {"targets": {"N3DSRuntime": "all"}}}
    frameworks = ("UIKit Foundation Metal QuartzCore AVFoundation CoreGraphics CoreMedia CoreVideo CoreImage CoreText "
                      "AudioToolbox CoreAudio CoreMotion "
                      "GameController CoreHaptics CoreBluetooth VideoToolbox Security CFNetwork Network "
                      "SystemConfiguration Accelerate IOSurface").split()
    app_dependencies = [{"target": "N3DSRuntime"},
                        {"package": "SharedDependencies", "product": "SharedDependencies", "link": False},
                        {"package": "SharedDependencies", "product": "CytrusSDL3"},
                        {"package": "OpenSSL", "product": "OpenSSL"}]
    app_dependencies += [{"sdk": f"{name}.framework"} for name in frameworks]
    app_dependencies += [{"sdk": "libz.tbd"}, {"sdk": "libiconv.tbd"}]
    if smoke:
        spec["targets"]["CytrusSmoke"] = {
            "type": "application", "platform": "iOS",
            "sources": [str(Path(__file__).resolve().parents[1] / "ios/tests/SmokeMain.mm"),
                        {"path": str(work / "homebrew/smoke.3dsx"), "buildPhase": "resources"}],
            "dependencies": app_dependencies,
            "settings": {"base": {
                "PRODUCT_BUNDLE_IDENTIFIER": "com.zimmra.3dsnative.smoke",
                # The user approved iOS 26 to reuse the pinned binary libraries.
                # Locally compiled C++ objects can retain their lower floor.
                "IPHONEOS_DEPLOYMENT_TARGET": "26.0",
                "CODE_SIGNING_ALLOWED": "NO", "CLANG_CXX_LANGUAGE_STANDARD": "gnu++23",
                "HEADER_SEARCH_PATHS": [str(core / "include")],
                "GCC_PREPROCESSOR_DEFINITIONS": "$(inherited) " + definitions,
            }},
            "info": {"path": str(work / "cytrus-build-source/SmokeInfo.plist"), "properties": {
                "CFBundleDisplayName": "3dsnative Smoke", "UILaunchScreen": {},
                "UIFileSharingEnabled": True, "LSSupportsOpeningDocumentsInPlace": True,
                "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait"],
            }},
        }
        spec["schemes"]["CytrusSmoke"] = {"build": {"targets": {"CytrusSmoke": "all"}}}
    if game_resources:
        metadata = json.loads((game_resources / "game.json").read_text())
        title = metadata["title_id"].lower()
        spec["targets"]["N3DSGame"] = {
            "type": "application", "platform": "iOS",
            "sources": [str(Path(__file__).resolve().parents[1] / "ios/Sources"),
                        {"path": str(game_resources), "name": "Game", "type": "folder", "buildPhase": "resources"}],
            "dependencies": app_dependencies + [{"framework": str(work / "upstream/Folium/Dependencies/MoltenVK.xcframework"),
                                                 "embed": True, "codeSign": False}],
            "settings": {"base": {
                "PRODUCT_BUNDLE_IDENTIFIER": f"org.threedsnative.title{title}",
                "PRODUCT_NAME": "3dsnative", "IPHONEOS_DEPLOYMENT_TARGET": "26.0",
                "TARGETED_DEVICE_FAMILY": "1", "CODE_SIGNING_ALLOWED": "NO",
                "HEADER_SEARCH_PATHS": [str(Path(__file__).resolve().parents[1] / "host")],
            }},
            "info": {"path": str(work / "cytrus-build-source/GameInfo.plist"), "properties": {
                "CFBundleDisplayName": metadata["name"], "UILaunchScreen": {},
                "UIFileSharingEnabled": True, "LSSupportsOpeningDocumentsInPlace": True,
                "UIApplicationSceneManifest": {"UIApplicationSupportsMultipleScenes": False,
                    "UISceneConfigurations": {"UIWindowSceneSessionRoleApplication": [{"UISceneConfigurationName": "Game", "UISceneDelegateClassName": "N3DSSceneDelegate"}]}},
                "NSCameraUsageDescription": "Use the camera for supported game features.",
                "NSMicrophoneUsageDescription": "Use the microphone for supported game features.",
                "CFBundleURLTypes": [{"CFBundleURLName": "Simulator input", "CFBundleURLSchemes": ["n3dsnative"]}],
                "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
            }},
        }
        spec["schemes"]["N3DSGame"] = {"build": {"targets": {"N3DSGame": "all"}}}
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path.home() / "Development/3ds-work")
    parser.add_argument("--sdk", choices=["iphoneos", "iphonesimulator"], default="iphonesimulator")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Build the UIKit host with the original homebrew fixture")
    parser.add_argument("--game-resources", type=Path, help="Private Game folder containing game.json and the backup files")
    args = parser.parse_args()
    work = args.work.expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if work == repo or repo in work.parents:
        parser.error("--work must be outside the repository")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.smoke and not (work / "homebrew/smoke.3dsx").is_file():
        parser.error("Build the original fixture with app/tools/build_homebrew.py first")
    game_resources = args.game_resources.expanduser().resolve() if args.game_resources else None
    if game_resources and (game_resources == repo or repo in game_resources.parents):
        parser.error("Private game resources must be outside the repository")
    if game_resources and args.smoke:
        parser.error("Choose the homebrew smoke host or the game host")
    for tool in ("git", "xcodegen", "xcodebuild"):
        if not shutil.which(tool):
            parser.error(f"Missing prerequisite: {tool}")
    # Keep SwiftPM, compiler and temporary files with the work products.
    for child in ("tmp", "cache/swiftpm", "cache/cytrus-spm", "logs", "cytrus-build-source"):
        (work / child).mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(work / "tmp") + "/"
    folium = checkout(work, "Folium")
    if game_resources:
        run(["git", "sparse-checkout", "add", "Dependencies/MoltenVK.xcframework"], cwd=folium)
    run(["git", "submodule", "update", "--init", "--depth=1", "Cytrus"], cwd=folium)
    if run(["git", "rev-parse", "HEAD"], cwd=folium / "Cytrus") != CYTRUS_REVISION:
        raise RuntimeError("Cytrus revision differs from the reviewed core")
    source = checkout(work, "SharedDependencies")
    checkout(work, "Vulkan-Headers")
    checkout(work, "VulkanMemoryAllocator")
    checkout(work, "SPIRV-Headers")
    for name in ("BoostSerialization", "BoostIostreams"):
        boost = checkout(work, name)
        for header in (boost / "include").rglob("*.hpp"):
            bundled = source / "Sources/boost/include" / header.relative_to(boost / "include")
            if not bundled.is_file() or bundled.read_bytes() != header.read_bytes():
                raise RuntimeError(f"Boost source/header mismatch: {header}")
    shared = work / "public-deps/SharedDependencies"
    patch_dependencies(source, shared)
    core = prepare_core(folium / "Cytrus/System", work / "public-deps/Cytrus/System")
    build_source = work / "cytrus-build-source"
    spec = build_source / "project.json"
    spec.write_text(json.dumps(project(work, core, shared, args.smoke, game_resources), indent=2) + "\n")
    run(["xcodegen", "generate", "--spec", str(spec), "--project", str(build_source)])
    if args.prepare_only:
        return
    arch = "arm64" if args.sdk == "iphoneos" else "x86_64"
    platform = "device" if args.sdk == "iphoneos" else "simulator"
    log = work / f"logs/cytrus-build-{args.sdk}.log"
    scheme = "N3DSGame" if game_resources else "CytrusSmoke" if args.smoke else "CytrusCore"
    command = ["xcodebuild", "-project", str(build_source / "CytrusCoreProbe.xcodeproj"),
               "-scheme", scheme, "-configuration", "Release", "-sdk", args.sdk,
               "-jobs", str(args.jobs), "-derivedDataPath", str(work / f"builds/cytrus-{platform}"),
               "-clonedSourcePackagesDirPath", str(work / "cache/cytrus-spm"),
               "-packageCachePath", str(work / "cache/swiftpm"),
               "-packageAuthorizationProvider", "netrc", f"ARCHS={arch}",
               "ONLY_ACTIVE_ARCH=NO", "CODE_SIGNING_ALLOWED=NO", "build"]
    print(f"Building {arch}; compiler output: {log}", flush=True)
    with log.open("w") as output:
        subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT)
    library = work / f"builds/cytrus-{platform}/Build/Products/Release-{args.sdk}/libCytrusCore.a"
    symbols = subprocess.run(["xcrun", "nm", "-g", str(library)], check=True,
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    forbidden = ("ARM_Dynarmic", "DynarmicExclusiveMonitor", "JitEngine")
    if any(name in symbols for name in forbidden):
        raise RuntimeError("The iOS archive still references a CPU JIT implementation")
    print(f"Built interpreter-only core: {library}", flush=True)


if __name__ == "__main__":
    main()
