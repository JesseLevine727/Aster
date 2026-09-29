# Cache RTL

- `aster_l1_cache.sv` (Phase 4): a direct-mapped cache with 16 four-word lines;
  loads read-allocate, stores are write-through and no-write-allocate. It is the
  instruction and data cache of `aster_minimal`, and the ROM instruction cache
  of each hart in the coherent SoC. It supports one outstanding request,
  matching the PicoRV32 native bus, and refills a line as four lower-level word
  transactions. `verification/unit/tb_aster_l1_cache.cpp` is its executable
  contract (hit, miss, byte write, eviction, bypass).
- `aster_coherent_cache.sv` (Phase 6): the coherent data-cache controller of
  the two-hart SoC. One controller holds both harts' banks (16 four-word lines
  each, direct-mapped, write-back, write-allocate, MSI) and also serves DMA/NPU
  device traffic and flushes, so it is the point of coherence and processes one
  transaction at a time. Tests: `make coherent-cache`, `make coherent-litmus`.
- `aster_l2_cache.sv` (v1.1): an optional memory-side L2 between the coherent
  controller and memory — direct-mapped, read-allocate, write-through, shared
  RAM window only, and disabled by default. Contract: [`docs/l2.md`](../../docs/l2.md);
  test: `make l2-unit`.

v2 replaces this organization with per-core L1 caches that hit in a single
cycle; see the [v2 plan](../../docs/phase17-plus.md).
