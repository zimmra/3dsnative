# Execution strategy: measure an existing iOS emulator first

On 2026-11-03 the user clarified the actual deliverable: a ready-to-sideload unsigned Omega Ruby IPA,
with the game, update, controls and settings prepared. Static recompilation is optional. No JIT-enabling
procedure or other extra launch requirement is wanted. Preserve the private-input and unsigned-output
constraints while choosing the simplest route that achieves acceptable gameplay.

## Current decision

Assess and package an existing JIT-free iOS emulator before investing further in a new runtime or CPU
interpreter bridge. Validate performance on the user's actual phone; the Intel Simulator can establish
functionality but cannot establish that phone's sustained frame rate or thermals. The device model and
iOS version have been requested. The user identified the target as iPhone Duo (`iPhone19,4`);
[Apple's specifications](https://www.apple.com/iphone-duo/specs/) list its A20 Pro chip. This establishes
the hardware target, not its emulation frame rate. AOT remains an option if the emulator baseline is inadequate.

The earlier nathsou import and generated-C survey are a retained alternative, not a completed port.
No game has booted in this repository yet, and neither method has been measured on the target device.
The optional single-job AOT compile survey was interrupted after this strategy change; its existing
objects remain reusable. It did not complete and supplies no full-build timing result.

## Reproducing the emulator build probe

The game-independent development tool [build_cytrus.py](../../app/tools/build_cytrus.py) fetches pinned
public sources, generates a core-only XcodeGen project and builds an unsigned static library. It is
not the IPA packer and does not bundle or run a game:

```sh
source ~/Development/3ds-work/env.sh
python app/tools/build_cytrus.py --sdk iphonesimulator
python app/tools/build_cytrus.py --sdk iphoneos
```

Use `--prepare-only` to fetch and generate without compiling. The Simulator target is x86_64 and the
device target is generic arm64. Locally compiled runtime objects retain an iOS 17 floor, while the
app targets iOS 26: the user approved reusing upstream binaries that require iOS 26. Source checkouts, writable dependency
copies, Swift package caches, generated projects and logs stay in the work folder. SwiftPM binary
dependencies retain the checksums in their pinned manifests; OpenSSL and PLzmaSDK are pinned to the
revisions in Folium's resolved graph rather than upstream's moving branches.

The upstream Crypto++ Swift package omits x86 instruction flags that its intrinsic source files
require. The probe supplies AES/SHA/PCLMUL/SSE4.1 flags only to that package target and AVX2 function
attributes only to its three AVX2 implementation files, plus SSE4.2 attributes for its CRC kernel.
Crypto++'s existing capability dispatcher
selects those kernels at runtime. The arm64 build receives none of the x86 flags. This fixes the
compiler configuration rather than excluding cryptographic implementations. Package resolution uses
`-packageAuthorizationProvider netrc`: the default Keychain provider stalled even on public artifact
downloads on this Mac. Neither signing credentials nor game keys are required for this probe.

The iOS build defines `THREEDS_NO_JIT`, excludes Dynarmic and the CPU shader-code generators, and
guards their factories. Initialization forces CPU and CPU-shader JIT settings off even if a config
requests them. FastInterp and the existing shader interpreter remain available; Vulkan/Metal shader
generation is independent. Keep the shared ARM instruction tick model, which FastInterp also uses.
The prepared core copy adds the missing `<type_traits>` include required by Xcode 26.3. Internal
headers use project visibility to avoid flattening duplicate basenames into the installed headers.
The glslang header prefix is restored from the pinned package's own headers, not another SDK version.

An original ARM11 [smoke program](../../tests/homebrew/smoke.S) prints a marker through
`OutputDebugString` and calls `ExitProcess`. Its [builder](../../app/tools/build_homebrew.py) assembles
it with Clang and packages a relocation-free `.3dsx` using LLVM objcopy. On this Mac, objcopy is
available in the work folder's Rust LLVM tools:

```sh
python app/tools/build_homebrew.py --objcopy "$(rustc --print sysroot)/lib/rustlib/x86_64-apple-darwin/bin/llvm-objcopy"
```

The generated fixture is 128 bytes. Unicorn executed its ARM instructions and verified the exact
message followed by the exit SVC. This validates the fixture, not an iOS emulator boot; that remains
the next integration check.

`build_cytrus.py --smoke` additionally builds a UIKit integration host with that fixture. Its
`Documents/smoke-result.json` records loading, the debug SVC marker, the process-exit log and the
core status; passing requires all of these checks. It uses software graphics and null audio for the
CPU/kernel test, and does not validate game rendering or sound.

On 2026-11-03 the unsigned x86_64 app built and ran in the dedicated iOS 26.3.1 Simulator on this Intel
Mac. The structured result reports `passed`, `loaded`, `debug_svc` and `exit_svc` true, core status 0,
and both JIT settings false. The screen displays the passing result. Evidence remains under
`~/Development/3ds-work/logs/smoke-result.json`, `logs/smoke-simulator.log` and
`setup/smoke-simulator-pass.png`. Symbol inspection of the core archive found no `ARM_Dynarmic`,
`DynarmicExclusiveMonitor` or `JitEngine` references. The generic-arm64 build is in progress; it has
not been run on a phone. Omega Ruby has not booted yet.

The initial host skipped `Config{}`, leaving service LLE settings uninitialized and causing an
`unordered_map::at` exception. The host now follows the upstream lifecycle: initialize paths,
logging, config, input and networking before loading. `SetUserPath` expects a trailing separator;
construct it explicitly because NSString's path-component helper removes it. The software renderer
factory is enabled at its translation unit for the headless test; Vulkan remains the game default.

The tiny fixture has empty rodata/data segments. The upstream `Process::Run` tries zero-byte heap
allocations for both and logs two misleading out-of-space diagnostics; the code segment maps and
executes successfully. This loader diagnostic remains open. Fresh NAND defaults, the missing online
client certificate and the lack of a title ID in a homebrew `.3dsx` also produce initialization
diagnostics. The smoke result checks guest execution and exit, not a clean commercial-game log.

The minimal build omits the optional FFmpeg host video recorder. Guest AAC decoding remains on
the upstream FAAD2 implementation. Matching Boost 1.92 serialization and file-descriptor sources are
built locally for both architectures because Folium's bundled Boost frameworks omit Intel Simulator.
All 301 corresponding headers match the pinned dependency package byte for byte; the build tool
checks this before compiling them. No private game input is needed for these builds.
The shared package's CHD reader belongs to other console cores and is omitted. Its Swift PLzma
wrapper otherwise introduced duplicate object linkage. SDL and OpenSSL are dynamic frameworks and
must be linked/embedded by the final app rather than folded into the static runtime archive.

## Relevant existing implementations

- [Cytrus](https://github.com/folium-app/Cytrus) is the 3DS component used by
  [Folium](https://github.com/folium-app/Folium). Its current source contains a cached direct-threaded
  [FastInterp](https://github.com/folium-app/Cytrus/blob/main/System/core/arm/fastinterp/fastinterp.cpp)
  interpreter, separate from Dynarmic JIT. Its
  [Swift interface](https://github.com/folium-app/Cytrus/blob/main/Cytrus.swift) already accepts Metal
  layers, input and settings. This is the first baseline to assess. This source inspection does not
  establish Omega Ruby performance or complete CPU correctness.
- [Pocket Native](https://github.com/whovian64/pocket-native), inspected at
  `178f2b0f4f3444ed8d6af49fe5c130fe3094b598`, reports Omega Ruby support and has a small MIT-licensed
  Zakuro-derived CPU interpreter. Its renderer targets OpenGL ES 3.1 and its frontends target Linux and
  Android; it is a useful compatibility and CPU reference, with iOS integration still required.
- [fearkov/3dsrecomp](https://github.com/fearkov/3dsrecomp) still has no license identified in its GitHub
  repository metadata. Keep that recompiler as a reference. Do not infer its licensing from a fork's
  top-level license. Zakuro's separately MIT-licensed CPU code has distinct provenance.
- The imported nathsou implementation already has Metal rendering and an iOS route, but its CPU oracle
  has genuine VFP mismatches and previously hidden oracle errors. Do not call its differential tests
  passing. Its 4 GiB plus 64 KiB reservation also needs device validation.

## Selection evidence to collect

Build the existing iOS core without CPU JIT, apply the update by metadata, bundle the user's files locally,
and test title/new game/battle/save/reload. Check normal game speed and continuous audio, then sustained
frame pacing and thermals. Retain an explicit no-JIT setting and a startup log showing the selected CPU
backend. Package one game per app with a stable title-derived bundle ID and writable Documents saves.

Use the paid newer-Xcode runner only for a source closure that demonstrably requires that SDK. Keep all
retail inputs local. GPU shader compilation is a separate operation from CPU JIT and does not by itself
choose the game CPU execution method.
