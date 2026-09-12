# Cache RTL

Phase 4 contains `aster_l1_cache.sv`, a small direct-mapped cache controller
used twice by `aster_minimal`: once as an instruction cache and once as a data
cache. Each instance has 16 four-word lines. Loads read-allocate; stores are
write-through and no-write-allocate. MMIO is excluded by the SoC's cacheable
address predicates.

The controller intentionally supports one outstanding request, matching the
PicoRV32 native bus. It refills a line as four lower-level word transactions
and holds each lower request until `lower_ready`. The unit test in
`verification/unit/tb_aster_l1_cache.cpp` is the executable contract for hit,
miss, byte-write, eviction and bypass behavior. Shared L2 and coherence remain
later roadmap work.
