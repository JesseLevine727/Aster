# AsterBench

The first AsterBench workload is a deterministic RAM `memcpy` slice. It copies
64 words four times, validates the result, and emits one machine-readable
record:

```text
ASTERBENCH,version=1,name=memcpy,bytes=256,status=PASS,cycles=0x...,retired=0x...,memory_transactions=0x...,cache_accesses=0x...,cache_misses=0x...,dma_bytes=0x...,accelerator_cycles=0x...
```

Counter values are fixed-width hexadecimal `key=value` fields so a later
revision can be compared without depending on a libc formatter. Phase 4
populates cycles, the current instruction-retire proxy, native memory
transactions and L1 cache accesses/misses. DMA and accelerator fields remain
zero until their subsystems are connected.

Run the benchmark regression with:

```sh
make bench
```

The ELF, binary and ROM image are generated under `build/software/` and the
Verilator system test checks the complete record and its non-zero baseline and
cache counters.
