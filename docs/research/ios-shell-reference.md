# Lessons from ns2ios-shell

Reference reviewed on 2026-11-03:
[xinerqu/ns2ios-shell](https://github.com/xinerqu/ns2ios-shell), especially its
[shell design](https://github.com/xinerqu/ns2ios-shell/blob/main/docs/SHELL.md) and
[overlay inventory](https://github.com/xinerqu/ns2ios-shell/blob/main/.shell-overlay.txt).
The user has already used this packer for Switch backups.

## Patterns to retain

- Build and test game-independent runtime components separately from private game inputs.
- Stamp runtime interfaces and build provenance, and reject incompatible packer/runtime combinations
  before producing an IPA. Include the generated-code ABI and shader inputs in the 3DS version.
- Identify game and update versions from container metadata. Key address profiles by the actual code
  hash, and invalidate generated code when the update changes.
- Validate the final package: executable permissions, required resources, architecture, bundle identity
  and absence of unintended content in game-independent artifacts.
- Keep stage logs and reuse only complete outputs whose inputs match. Keep all work under the explicitly
  selected work root. Cleanup must remain inside that root.
- Use shader compilation progress and persistent diagnostics in the iOS frontend.

## Adaptation for ARM11 static recompilation

The Switch shell packages rewritten ARM64 modules into a segment-only guest library. For this project,
ARM11 instructions become generated C that must be compiled and linked into each game's iOS app.
The reusable artifact is the game-independent runtime library; each private game still needs the
device compiler and linker on this Mac. No generated game code or assets belong in CI.

The reference discusses increased-memory-limit handling by sideloaders. This is a separate capability
from extended virtual addressing: it does not establish that a 4 GiB guest VA reservation works.
Test the actual app and provisioning result. The user requires unsigned output and performs signing
and device installation themselves.

The reference's private-game CI smoke route does not apply here. Use homebrew-only smoke inputs in CI,
and keep every retail backup and key local. No reference implementation code has been imported.

## Additional handoff from the user's Switch integration

The user supplied a separate agent's handoff on 2026-11-03. Its observations are reference evidence;
its proposed milestones and permissions from the Switch task do not change this project's scope.
Keep unsigned delivery and the Intel Simulator development loop. The user subsequently authorized a
newer-Xcode runner for components that require Xcode 27.x, with paid-runner use minimized. All compatible
work stays on the Intel Mac with Xcode 26.3.

The handoff points to the user's pinned
[Duo integration workflow](https://github.com/zimmra/ns2ios-shell/blob/909d51aed053ffc549866cce427ca4ef9209489b/.github/workflows/duo-shell.yml)
and
[artifact verifier](https://github.com/zimmra/ns2ios-shell/blob/909d51aed053ffc549866cce427ca4ef9209489b/dm/ios/tools/duo_shell.py).
The newer-SDK game-free executable plus locally compiled game-module approach is the candidate for
newer-SDK features. It needs a homebrew validation of the exported
ABI, cross-SDK linking, module loading and device behavior. Do not infer physical hinge support from
an older-SDK build, a manifest or symbol presence alone.

The pinned 3dsrecomp CMake file already has a multi-game module mode using `-bundle_loader`, alongside
the single-game static-library mode. Assess it before introducing another module ABI. The requested
single-game static build remains the initial route; a separately reusable executable is not yet tested.

The runner boundary is explicit: keep extraction, update application, surveys, ARM11-to-C generation,
private game-module compilation, compatible shader compilation and final IPA assembly local. Retail
inputs never reach the runner. Build the newer-SDK game-free shell and required integration objects
on the runner only when their source, SDK, deployment target or exported ABI changes; cache and
download that immutable artifact. Prove artifact consumption with an inert/homebrew module first.
The exact minimum newer-SDK source closure still needs measurement; do not assume every runtime
component requires a paid build.

Carry these concrete failure checks into the 3DS packer and frontend:

- Separate metadata probing from completed extraction with an applied update. Record the effective
  code hashes before recompilation; never reuse a base-only extraction as an updated build.
- Write canonical ZIP archives with unique paths and executable permissions. Validate the final IPA
  through Apple tooling as well as the packer's own ZIP reader.
- Use one layout snapshot for drawing and input, with separate top-screen, bottom-screen and control
  rectangles. Invert the displayed bottom-screen transform for stylus input and reject bars/controls.
- Clear touch-owned state on geometry changes and cancellation while preserving independently owned
  physical-controller input. Device rotation and gyro behavior need explicit device milestones.
- Probe the exact guest virtual reservation before retail code, logging requested size and `errno`.
  Validate extended-VA entitlement availability independently of increased resident-memory limits.

The handoff's successful Switch signing/export results establish neither 3DS gameplay nor authorization
to sign this project's builds. No account, keychain or signing-helper operation was performed here.
