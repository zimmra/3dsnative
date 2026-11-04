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
device target is generic arm64, both with iOS 17 deployment. Source checkouts, writable dependency
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
