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
Omega Ruby now boots and renders in the Intel Simulator with the interpreter; neither method has been measured on the target phone.
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
message followed by the exit SVC. The subsequent iOS integration check also passed, as recorded below.

`build_cytrus.py --smoke` additionally builds a UIKit integration host with that fixture. Its
`Documents/smoke-result.json` records loading, the debug SVC marker, the process-exit log and the
core status; passing requires all of these checks. It uses software graphics and null audio for the
CPU/kernel test, and does not validate game rendering or sound.

On 2026-11-03 the unsigned x86_64 app built and ran in the dedicated iOS 26.3.1 Simulator on this Intel
Mac. The structured result reports `passed`, `loaded`, `debug_svc` and `exit_svc` true, core status 0,
and both JIT settings false. The screen displays the passing result. Evidence remains under
`~/Development/3ds-work/logs/smoke-result.json`, `logs/smoke-simulator.log` and
`setup/smoke-simulator-pass.png`. Symbol inspection of the core archive found no `ARM_Dynarmic`,
`DynarmicExclusiveMonitor` or `JitEngine` references. The generic-arm64 device build also succeeded
locally in Xcode 26.3 with an iOS 26 app minimum and passed the same archive-symbol check. It has
not been run on a phone. The commercial-game boot is recorded below.

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

## Omega Ruby and unsigned packing checkpoint

The private decrypted cartridge boots with the matched update in the Intel iOS 26.3.1 Simulator.
Both screens render through Vulkan/MoltenVK into separate CAMetalLayers. The language-selection
screen displays **Ver. 1.4**, confirming the display version despite the CIA's TMD version being
7280 rather than the initially expected 5120. Simulator-only local input selected English and
advanced to the Begin game confirmation. This establishes boot, rendering and button response;
it does not establish a title-screen/new-game/battle/save/reload route or phone performance.

The host obtains filesystem representations inside its boot thread's autorelease pool. Obtaining
them earlier on the UIKit thread left temporary C buffers invalid during update installation.
The update installs through the existing AM CIA implementation into Documents on first launch;
a SHA-256 marker and installed-content check skip unchanged subsequent installs. The base CCI and
CIA remain private bundle resources; this interpreter route does not need generated game C.

Intel Simulator Metal rejects linear textures backed by shared buffers. The Vulkan texel-buffer
adaptation uses a private GPU ring and copies committed mapped-upload ranges with transfer/shader
barriers outside render passes. Device builds retain the upstream shared-buffer path. This fixes
the observed Metal assertion without omitting guest rendering. Simulator images remain local under
`~/Development/3ds-work/setup/`; none are committed or uploaded.

[build_ipa_from_3ds.py](../../scripts/build_ipa_from_3ds.py) validates decrypted NCCH/CIA hashes,
selects the highest matching update by TMD title ID/version, caches private inputs outside the repo,
builds the generic-arm64 iOS 26 host and creates an unsigned Payload archive. The app executable is
checked for an unexpected signature and provisioning profile before packaging. Source checkouts and
binary library revisions are pinned by the runtime builder. No paid runner was needed for this
checkpoint. The original encrypted `.3ds` is intentionally rejected; use the supplied decrypted CCI:

```sh
source ~/Development/3ds-work/env.sh
python scripts/build_ipa_from_3ds.py "/Volumes/ZNAS/Media/Content/Games/ROMs/library/roms/n3ds/Pokemon Omega Ruby-decrypted.cci"
```

The end-to-end command produced `~/Development/3ds-work/PokemonOmegaRuby.ipa`. This is a development
candidate, not a gameplay-verified release. The shell adapts nxnative's CAShapeLayer gamepad for
3DS buttons and a visible C-stick, merges independent touch/keyboard/controller inputs, maps game
touches only to the lower screen, and offers resolution changes. Touch controls remain visible by
default even when a controller connects; automatic hiding is optional. Files and saves use Documents.
The frontend offers an FPS display, and logs performance every ten samples for the phone test.
It still needs the final Duo fold/motion adaptation, Checkpoint import UI and
full gameplay verification. The existing SDL-backed audio path is retained for the baseline rather
than introducing an untested replacement. Repository cleanup and documentation migration remain
pending the phone performance decision.

The original homebrew smoke test and 16 synthetic container/packaging tests are the reproducible
checks so far. No iPhone was signed or installed by the agent. Full speed, sustained thermals,
battle correctness, saving/reloading, imported saves and sampled software-renderer comparisons remain
unverified. If this interpreter baseline is too slow on the A20 Pro, resume the retained AOT option.

## Native Duo, audio and display update

The user reports that the interpreter build runs well on their iPhone Duo. They also report silent
audio, SDK 26 compatibility presentation and crowded control groups. The interpreter is therefore
the current execution route; AOT is retained as research rather than required for this game.

The first shell had no Playback audio session, and upstream Auto sink ordering selected OpenAL
before RemoteIO. The host now establishes/re-establishes a Playback session, explicitly chooses the
CoreAudio sink, and uses a RemoteIO implementation that installs its callback before starting the
unit. It uses the actual emulated input sample rate, reports AudioUnit errors, and disposes the unit
on shutdown. The previous upstream sample-rate query used an uninitialized property-size field
and a 32-bit integer for a Float64 property; the sink now returns the known input format rate.
The local Simulator log confirms output type 7 and RemoteIO startup at 32728 Hz. Audible output and
interruption recovery on the phone still require the user's confirmation.

The working ns2ios-shell integration is the reference for scene-backed windows, native hinge updates,
reserved-region observation (including inactive zero-height folds), current-window drawable scale,
and touch cancellation on geometry changes. The new portable geometry keeps rendering and stylus
mapping in agreement. Portrait and tabletop poses ignore saved landscape preferences. Regular
landscape offers 50/50 side-by-side, top only, bottom only and large/small vertical; the vertical
preset exposes a flip button. Four separate control-group anchors replace the crowded paired
stick/D-pad and face/C-stick arrangement. Hidden guest screens retain offscreen swapchains so
single-screen presentation does not suppress guest GPU work.

[Apple's Duo preparation guide](https://developer.apple.com/iphone-duo/prepare/) explains why the
SDK 26 executable cannot enable full native Duo presentation through plist changes alone.
[duo_shell.py](../../app/tools/duo_shell.py) exports only the public runtime static archive and three
public frameworks from the local build. It records source and file hashes and refuses private game
formats. The manual [Duo workflow](../../.github/workflows/duo-shell.yml) compiles only the UIKit shell
and links the final executable with Xcode 27.1. The first successful
[runner job](https://github.com/zimmra/3dsnative/actions/runs/41784131476) completed in 39 seconds.
It verifies actual Mach-O SDK 27.1, native hinge/reserved-region symbols, scene metadata and the
absence of private game resources. These checks establish compiled native support; physical fold
and display-handoff behavior still require a phone test.

The downloaded reusable app lives under `~/Development/3ds-work/native-duo-shell/3dsnative.app`.
The packing command automatically verifies/reuses it, preserves that newer-SDK executable and adds
all private game resources locally. It decodes the backup's tiled RGB565 SMDH icon into opaque iOS
icon PNGs locally; neither artwork nor backups enter the runner or Git. The stable game bundle ID
is unchanged. The final unsigned output remains `~/Development/3ds-work/PokemonOmegaRuby.ipa`.

The current synthetic suite includes five tests of the actual C geometry function and an icon
format/tile test, in addition to container/update/packaging checks. Together with the explicit
right-control ordering check they cover 23 tests. Per the user's follow-up, ABXY is the rightmost
group; the C-stick sits below and inward toward the center, rather than on the centerline.
Runtime-source and shell-source hashes are separate: changing UIKit geometry requires only another
short shell compile/link on the runner, while the local ARM11 runtime remains reusable.

## Audio sample ABI correction and larger controls

The next device report confirms sound is audible but heavily distorted. The pinned SoundTouch
archive exports `SoundTouch::putSamples(short const*, unsigned int)` on both arm64 and x86_64,
but its shipped STTypes header defaults to float PCM. The time-stretcher calls its sample methods
virtually, so incompatible sample-pointer types did not cause an unresolved-symbol error. Float
buffers were interpreted as integer PCM and vice versa.

A new synthetic stereo probe runs the actual `AudioCore::TimeStretcher` from the linked iOS runtime.
With the old compile flags, 440/880 Hz inputs measured about 314.74/558.51 Hz, with incorrect RMS
levels. With `SOUNDTOUCH_INTEGER_SAMPLES=1` applied specifically to the time-stretcher translation
unit, the same probe measures 439.88/879.75 Hz and RMS 7071.29/4242.63, matching the 10000/6000-peak
input signals. The ARM11 debug/exit smoke test also passes. Evidence remains in the work folder's
`logs/audio-probe-before.json` and `logs/audio-probe-after.json`; the fixture itself is original
synthetic audio, not a game recording.

The source adaptation statically checks the header sample type and the build checks the archive's
integer-PCM symbol, preventing silent ABI drift. The DSP stretch threshold is also corrected from
`95` to `0.95`: its performance statistic is a normalized ratio (1.0 is full speed). Initialize the
stretcher sample rate before installing the live sink callback, and append FIFO samples after any
flushed samples instead of overwriting their start. These are general audio-path fixes, not game
workarounds or a blanket disabling of stretching.

The user also requested modestly larger controls using the working Switch interface as reference.
Increase the shared control scale and minimum sizes by 25%, retain ABXY as the outer right group,
and keep the C-stick inward. The right stick now scales with the reference 60:77 right/left radius
proportion instead of remaining a fixed-size nub on the expanded display. Its knob and hit area
follow the same radius. Native Duo display presets, icon and stable bundle identity are retained.
This delivery uses bundle build number 3 so it is distinguishable from the preceding build.

## Consistent inner-display controls and batch packing

The user confirms build 3's audio, native Duo presentation and performance work well. Build 4
uses the approved portrait control size as an orientation-independent scale for book and landscape
layouts. Flat portrait shares the book-style upper/lower panel arrangement, honoring a reported
inactive hinge division as well as a bent pose. Landscape preferences still apply only to regular
landscape. In stacked layouts, Select/Menu/Start and the vertical-layout flip button move to the
bottom row. The smaller display expands into the freed space between control hit targets; its
stylus region excludes their actual grab radii. Vertical flip swaps which guest screen is large
while retaining the large screen above the small one.

Thirty synthetic tests cover the portable geometry, equal control scale, touch-target exclusion,
container metadata, update selection, icons and unsigned ZIP packing. The revised UIKit sources
also compile locally for the Intel iOS Simulator. Physical Duo geometry remains a user check for
each layout revision. The ARM11/audio runtime is unchanged, so only one short public shell build
is required on the newer-Xcode runner.

The batch command is `python3 scripts/build_ipas_from_3ds.py "/path/to/backups"`. It selects one
decrypted base per title and a matching newest decrypted update. An encrypted duplicate of the
same update version does not make that selection ambiguous; a newer encrypted-only update still
blocks a silent downgrade. After local packing, every archive is checked for CRC integrity,
unique paths, native shell identity, executable permissions, title identity, icons, input hashes
and absence of signature resources. All backups, artwork, local caches, verification reports and
IPAs stay outside Git. Packaging other titles establishes archive integrity, not gameplay support;
Omega Ruby remains the title with user-reported device gameplay.
