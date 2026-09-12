# Boot firmware

Boot images establish the execution environment used by Aster. The current
`hello.c` image runs through `software/runtime/start.S`, uses a RAM stack and
`.bss`, then writes its result to the UART.
