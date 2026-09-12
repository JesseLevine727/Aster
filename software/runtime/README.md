# Bare-metal runtime

`start.S` is the current runtime entry point. It initializes the RAM stack,
copies initialized data, clears `.bss`, calls `main`, and halts if `main`
returns. The linker reserves a 4 KiB, 16-byte-aligned stack and aligns the
word-copy/clear boundaries. `aster.h` provides UART and performance helpers.
`make runtime` checks initialized data, BSS and recursive stack use from
poisoned RAM across two boots; `make host-tests` checks layout limits.

Future additions include trap handling, synchronization primitives, drivers,
and extended services required by later AsterBench workloads.
