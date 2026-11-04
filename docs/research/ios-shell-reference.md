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
