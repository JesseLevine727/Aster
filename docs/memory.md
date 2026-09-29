# Memory use and OOM containment

This note explains why Aster's later phases started exhausting RAM, and how to
run the heavy jobs so a system-wide OOM can never happen again.

## Symptom

`systemd-oomd` (the userspace OOM killer) began killing processes in the user
session during Phase 16:

```text
Sep 27 01:42  app-gnome-google-chrome-stable.scope: killed by the OOM killer
Sep 27 05:40  p16-gl-ecg.service: killed by the OOM killer (after 2h 35m CPU)
Sep 27 05:47  tmux-spawn-...scope: killed by the OOM killer
Sep 27 08:36  app.slice: a process was killed by the OOM killer
```

plus one kernel OOM earlier at ~61 GiB. The desktop apps and the build jobs
share one machine (60 GiB RAM + 8 GiB swap), and `app.slice` peaked at
**55.1 GiB**.

## Why it started at Phase 16

Phase 15 built a *minimal* block; Phase 16 is the first **full chip**
(556 k instances, 680 MB GDS). Several signoff steps scale with the design and
spike hard. Peak RSS per step, from LibreLane's own `*.process_stats.json`:

| Step | Peak RSS (p16-f2 / p16-clean) |
|------|------------------------------:|
| `magic-writelef` | **32 GiB / 54 GiB** |
| `klayout-drc` | 25 GiB |
| `magic-spiceextraction` | 21 GiB |
| `netgen-lvs` | 13 GiB |
| `magic-drc` | 13 GiB |
| `openroad-detailedrouting` | 11 GiB |
| `openroad-irdropreport` | 11 GiB |
| `klayout-xor` | 9 GiB |

For comparison, the Phase 15 runs peaked at ~3 GiB per step. The gate-level
Icarus simulation of the 199 MB Phase 16 netlist adds ~10 GiB (2.5 GiB steady
state for `vvp`, ~10 GiB to elaborate).

Three things then turn a big-but-survivable step into an OOM:

1. **No cgroup cap anywhere.** The user slice has `MemoryMax=infinity` and the
   `systemd-run --user` units used for the heavy jobs set no `MemoryMax`.
2. **`systemd-oomd` is configured to kill the user session.**
   `/usr/lib/systemd/system/user@.service.d/10-oomd-user-service-defaults.conf`
   sets `ManagedOOMMemoryPressure=kill` and `ManagedOOMMemoryPressureLimit=50%`.
   When the session's memory pressure exceeds 50% for 30 s, oomd kills a process
   in it — often chrome or the long-running simulation.
3. **Concurrent heavy jobs.** Running LibreLane (`magic-writelef` at 32-54 GiB)
   and an Icarus gate-level sim (10 GiB) at the same time, with the desktop on
   top, exhausts memory even before swap helps.

## Fix: run heavy jobs through `scripts/memguard.sh`

`scripts/memguard.sh` wraps a command so it (a) holds a global `flock`, so only
one heavy job runs at a time, and (b) runs in a cgroup with a hard `MemoryMax`,
so a runaway is killed *inside its own cgroup* instead of taking the desktop
down. It also sets `MALLOC_ARENA_MAX=2` and caps OpenMP threads.

`scripts/run_asic.py` applies the wrapper itself, so run it directly and set the
caps through the environment. Do **not** wrap `run_asic.py` in `memguard.sh`
again: the nested `flock` on the same lock file deadlocks (observed on
2026-09-27). Wrap other heavy commands, such as gate-level simulation, directly:

```sh
python3 scripts/run_asic.py --design v1 --run-tag p16-f2
scripts/memguard.sh --high 12G --max 16G --swap 8G -- \
    /home/elfo/tools/iverilog/usr/bin/vvp v1_tb_c.vvp +rom=... +timeout=...
```

Defaults: `--high 48G --max 56G --swap 32G`. The high cap must stay above
`magic-writelef`'s ~54 GiB peak; the gate-level sims can use a much smaller cap.
Override with `ASTER_MEM_HIGH` / `ASTER_MEM_MAX` / `ASTER_MEM_SWAP_MAX`.

**Never launch LibreLane and Icarus (or two LibreLane runs) directly in
parallel.** `memguard` serialises them; if you bypass it, use the same
`flock /tmp/opencode/aster-heavy.lock` yourself.

## The `magic-writelef` spike is not fundamental

`Magic.WriteLEF` reads the 536 MB, 3.98 M-instance DEF into Magic's flat layout
database and emits a **370-line** abstract LEF — 54 GiB and 1h15m single-threaded
for a 14 KB file. The cost is entirely Magic's per-cell-use memory model, not a
requirement of the flow:

- The top-level LEF's **only consumer** is `Odb.CheckDesignAntennaProperties`,
  which *prints warnings* if the LEF lacks antenna info. It gates nothing.
- `Magic.WriteLEF`'s output is otherwise just the `final/lef/aster_v1_asic.lef`
  deliverable, which only matters if the chip is instantiated as a hierarchical
  block. Aster is a flat single-block SoC, so it is not needed.

Skip both steps and the 54 GiB spike disappears:

```sh
ASTER_MEM_HIGH=16G ASTER_MEM_MAX=24G \
  python3 scripts/run_asic.py --design v1 --run-tag p16-f2 \
      --skip-step Magic.WriteLEF --skip-step Odb.CheckDesignAntennaProperties
```

`MAGIC_LEF_WRITE_USE_GDS=1` switches the input from the DEF to the GDS but does
not change the per-instance cost, so it is not a fix. If a future phase needs the
top LEF (hierarchy), run that one step alone with enough swap.

## Fix: give the big step headroom

Even with the LEF write skipped, `magic-spiceextraction` (21 GiB) and
`klayout-drc` (25 GiB) still spike, so keep some headroom. 8 GiB of swap is thin
for a 25-54 GiB spike on a 60 GiB box. Grow it (root):

```sh
sudo fallocate -l 32G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
# persist in /etc/fstab:
# /swapfile none swap sw 0 0
```

## Fix: stop oomd killing the session

`memguard` prevents the pressure from forming, but to stop oomd from killing
interactive apps during an unrelated spike, raise its threshold (root):

```sh
sudo mkdir -p /etc/systemd/oomd.conf.d
printf '[OOM]\nDefaultMemoryPressureLimit=80%%\nSwapUsedLimit=95%%\n' \
    | sudo tee /etc/systemd/oomd.conf.d/aster.conf
sudo systemctl restart systemd-oomd
```

Or exempt the user session's batch jobs by creating
`/etc/systemd/system/user@.service.d/oomd.conf`:

```ini
[Service]
ManagedOOMMemoryPressure=auto
ManagedOOMMemoryPressureLimit=90%
```

then `sudo systemctl daemon-reload`. Disabling oomd entirely
(`sudo systemctl disable --now systemd-oomd`) is the last resort — the kernel
OOM killer then picks a victim, which is usually a batch job rather than the
desktop, but it is less predictable.

## Applied on this machine (2026-09-27)

- `/swapfile` added, 32 GiB (total swap 40 GiB), persisted in `/etc/fstab`.
- `/etc/systemd/oomd.conf.d/aster.conf`: `DefaultMemoryPressureLimit=85%`,
  `SwapUsedLimit=95%` (was 60% / 90%).
- `/etc/systemd/system/user@.service.d/oomd.conf`:
  `ManagedOOMMemoryPressure=auto`, `ManagedOOMMemoryPressureLimit=85%`
  (was `kill` / 50%).
- `systemd-oomd` restarted.

## Checklist before a long run

- [ ] Close the heavy desktop apps (chrome, codex, MATLAB) or accept that they
      share the budget.
- [ ] Launch through `scripts/memguard.sh` (serialises + caps).
- [ ] For LibreLane, keep `--high` above the largest step (`magic-writelef`).
- [ ] For gate-level sims, cap tightly (`--high 12G --max 16G`).
- [ ] Confirm swap is enabled (`swapon --show`).
