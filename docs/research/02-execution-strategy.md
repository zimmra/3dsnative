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
