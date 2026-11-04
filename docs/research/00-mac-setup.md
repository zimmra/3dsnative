# Local setup for the 3DS port

Observed on 2026-11-03. This is a setup record, not a claim that the 3DS runtime or a game boots.

| Check | Result |
| --- | --- |
| Host | Intel `x86_64`, macOS 15.8.2 (24H232) |
| Resources | 8 GiB RAM, four logical CPUs; keep heavy build concurrency low |
| Xcode | 26.3 (17C529) |
| Device and Simulator SDK | iOS 26.2 |
| Installed iOS runtime | iOS 26.3.1 (23D8133) |
| Space available at the initial check | 830 GiB on the Development volume |
| Dedicated Simulator | `3dsnative iPhone 16e`, `186D240F-1D85-4638-B0C1-648BEF1560DB` |
| Simulator boot | `simctl bootstatus -b` completed successfully; screenshot saved outside the repository |
| Device compiler | arm64 iOS 17 C object compiled successfully |
| Simulator compiler | x86_64 iOS 17 C object compiled successfully |
| Metal | Installed the missing Metal Toolchain 17C7003j; `-std=metal3.0` with iOS 17 compiled and linked a probe metallib |
| XcodeGen | 2.46.0 generated a static-library project; unsigned x86_64 Simulator build succeeded |
| CMake/Ninja | 4.4.5 / 1.13.2 built static-library probes for both device and Simulator |
| Python | Homebrew 3.14.10; isolated `3ds-work/venv`, `pip check` passed |
| Python CPU tools | Capstone distribution 5.0.9 and Unicorn 2.1.4 decoded/executed an ARM instruction successfully |
| Native libraries | Homebrew Capstone 5.0.9, Unicorn 2.1.4 and SDL3 3.4.18; native link/run probe passed |
| Optional interpreter research tools | Rust 1.95.0, arm64-device and x86_64-Simulator targets, LLVM tools; all under `3ds-work` |

All probes, output and the upstream inspection checkout are in `~/Development/3ds-work`.
`env.sh` there configures a work-local temporary directory and caches for subsequent commands.
CoreSimulator and the Xcode component installer require access outside the agent filesystem sandbox.

## Backup preflight

The NAS was mounted and opened read-only. The original `Pokemon Omega Ruby.3ds` has its main NCCH
NoCrypto flag clear and was rejected. The replacement `Pokemon Omega Ruby-decrypted.cci` has NoCrypto
set in every present partition; its main exheader and ExeFS superblock match the NCCH plaintext hashes.
Its base program ID is `000400000011C400`.

The supplied update CIA has an unencrypted content record and NoCrypto set in its NCCH. Its TMD and
NCCH identify `0004000E0011C400`. Its TMD title version is **7280 (`0x1C70`)**, rather than the task's
expected 5120 (`0x1400`); the filename alone does not establish its displayed version. Confirm the
game's displayed update version during bring-up.

Header interpretation follows [NCCH](https://www.3dbrew.org/wiki/NCCH) and
[title metadata](https://www.3dbrew.org/wiki/Title_metadata). No keys were needed or printed.

## CPU oracle setup and first result

The inspection checkout of [nathsou/3dsrecomp](https://github.com/nathsou/3dsrecomp) is pinned locally to
`8737b7277a10acee19116de6e9e31fa6a6647340`. It already includes iOS support that was absent from the
task's initial description; assess that code before designing replacement interfaces.

With Capstone 5.0.9 and Unicorn 2.1.4 in `3ds-work/oracle-venv`, this command ran on the Intel host:

```sh
TMPDIR=~/Development/3ds-work/tmp/ \
  ~/Development/3ds-work/oracle-venv/bin/python -m tools.test_cpu 2000 1
```

Run from the upstream checkout. Result: **3/2000 cases failed**, all VFP cases involving `vsqrt.f32`:
host `ffc00000`, Unicorn `7fc00000`. The generator emits host `sqrtf` directly. Exact ARM NaN behavior
needs explicit handling and further tests in the portability phase. This is not a passing differential
suite. The upstream script also returns exit status zero after reporting failures; fix that before CI
relies on its status. No generator changes have been made yet.

After importing the pinned source, the test runner was changed to return failure for mismatches or
oracle errors and report the actual comparison count. The same seed compared **1,671** cases, with
three VFP mismatches. It exposed 329 previously silent oracle errors: 308 invalid instructions,
eight unmapped writes, eleven unmapped reads and two CPU exceptions. The generator is unchanged.

The later Python 3.14 environment passed its dependency and decode/execute checks. Capstone's 5.0.9
distribution reports `5.0.7` through its module's `__version__`; record distribution metadata when
checking its installed version. The full differential result above came from the initial oracle environment.

## Private extraction and AOT survey

Both validated inputs extracted successfully under `3ds-work/PokemonOmegaRuby`; each has 145 CROs
and one CRS, with identical module filenames. The updated main code's SHA-256 is
`ff2ce8f4e95ce7dd0448629be43a8e6524df8ed81a848f52bddc5da06ffd7e80`.
The update-only code/CRO analysis generated 79,944 functions, 331,012 dispatch entries and 331,061,254
bytes of C across 215 files in 112.33 seconds wall time. Maximum RSS was 2,709,475,328 bytes and peak
memory footprint was 2,946,740,224 bytes. One discovered Thumb UDF remains an analysis finding.

This is an update-code survey, not an applied-update game build: the base and update RomFS images
remain separate. All generated C, binaries and full module listings stay outside the repository.
The single-job Simulator compile was deliberately interrupted after the user made AOT optional;
there is no completed compile-time measurement. See [execution strategy](02-execution-strategy.md).

The user reports sideloading with Sideloadly, iLoader and Impactor. Available virtual-address
entitlements still require device verification through the chosen signing method.
