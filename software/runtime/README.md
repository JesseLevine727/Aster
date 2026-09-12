# Bare-metal runtime

`start.S` is the current runtime entry point. It initializes the RAM stack,
copies initialized data, clears `.bss`, calls `main`, and halts if `main`
returns. `aster.h` provides the first explicit UART console primitive.

Future additions include trap handling, synchronization primitives, drivers,
and the small runtime used by AsterBench.
