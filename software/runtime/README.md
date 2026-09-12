# Bare-metal runtime

`start.S` is the current runtime entry point. It initializes the RAM stack,
copies initialized data, clears `.bss`, calls `main`, and halts if `main`
returns. The linker reserves a 4 KiB, 16-byte-aligned stack and aligns the
word-copy/clear boundaries. `aster.h` provides UART and performance helpers.
`make runtime` checks initialized data, BSS and recursive stack use from
poisoned RAM across two boots; `make host-tests` checks layout limits.

The Phase 5 `start_multicore.S` / `link_multicore.ld` pair is a separate runtime
for the new multicore map; do not load it into the legacy single-core overlay.
Each hart reads its MMIO ID before using a stack. Hart 0 alone initializes
shared data/BSS; each hart clears its own private BSS and uses its own 4 KiB,
16-byte-aligned stack. Hart 0 enters `main`, hart 1 enters
`aster_secondary_main`, and either return parks that hart. Hart 0 explicitly
releases the secondary after publishing initialized shared state.

`aster_multicore.h` supplies polling mailbox, publication-fence and worker
reset/release helpers. Shared globals are always uncached. Private zero-
initialized storage uses `ASTER_PRIVATE0` / `ASTER_PRIVATE1` (NOLOAD sections;
do not put nonzero initializers there). Only the owning hart may access each
private region, including its stack. Firmware cannot pass stack/private data
pointers to another hart; copy into shared RAM and publish ownership instead.
See the [full contract](../../docs/phase5.md).

`make multicore-runtime-matrix` tests one/two real harts, cached/uncached and
four memory timing modes. `make host-tests` also rejects private/stack/shared
overflows and checks separate stack symbols and initialized odd-byte images.
Trap handling and general-purpose atomics remain future work.
