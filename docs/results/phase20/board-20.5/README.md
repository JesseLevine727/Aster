# Milestone 20.5, step 5: the board run

The text of record is the "Step 5" section of 20.5 in
[`../../../phase20.md`](../../../phase20.md). The plan is
[`../../../tuning.md`](../../../tuning.md) §7. `SHA256SUMS` covers every file
here.

**The image** is step 4's final build,
`build/fpga/pynq_z1/aster_soc-c8k-default-o5asm-mgi/aster_soc.bit`:
- its sha256 is 24a41d81…, recorded in the bundle,
  `../matrix-20.5/timing/final-build/bitstream.sha256`;
- its configuration data is 0bf4b4c3…, as `../matrix-20.5/timing/timing.txt`.

Each load programs it through the PYNQ-Z1's FPGA manager. FCLK0 is set to
100 MHz and measured, and the SoC's identity registers are read.

**The programs** are every captured R (soc_dev) entry of the final capture,
b96e48b (`../matrix-20.5`): 5,254 firmware images, each as the matrix built
it. CoreMark's official run (tuning.md §7.3) is captured separately from
1222fd3 (`coremark/capture`). It is two programs: the performance run, which
gives the score, and the validation run, which CoreMark's run rules ask to
validate too. Each program must end as simulated:
- the same status;
- tohost written at the same cycle;
- hart 0's instructions retired at tohost;
- the window's cycles and instructions retired;
- the console byte for byte, so every record and counter in it;
- the console reader at most 8 KiB behind (half the 16 KiB ring).

**The files:**
- **`reference.json.xz`**, `coremark/reference.json`: every program simulated as
  the board runs it, by `scripts/matrix_board_reference.py` (tb_soc's `+board`
  mode, which reads the board's registers). It runs on the capture's own
  simulation, its sha256 checked against every entry's. All end as captured
  (`reference.log`).
- **`boot1/`, `boot2/`:** the two loads of record, each with every R program.
  - `report.json.xz`: the board's own report, each program's console included,
    from `scripts/aster_board_remote.py`.
  - `comparison.json.xz`: `scripts/matrix_board.py --reference`'s comparison.
  - `remote.log.xz`: the board's log.
  - `matrix_board.log`: the verdict.

  | Load | As simulated | Largest console lag | FCLK0 measured | Loading, running |
  | --- | ---: | ---: | ---: | ---: |
  | 1 | 5,254 of 5,254 | 4,890 bytes | 100.0001 MHz | 1,254 s, 109 s |
  | 2 | 5,254 of 5,254 | 4,843 bytes | 99.9991 MHz | 1,260 s, 110 s |
- **`coremark/boot1/`, `coremark/boot2/`:** both CoreMark runs on two further
  loads, each as simulated.
  - The performance run prints its known 2K CRCs, "Correct operation
    validated" and "CoreMark 1.0 : 218.737829".
  - The validation run prints its known 2K validation CRCs and "Correct
    operation validated".
  - FCLK0 measured 99.9986 and 99.9993 MHz.
- **`coremark-performance-only/`:** two earlier loads of the performance run
  alone, from 7cee16e's capture, both as simulated. They ran before the
  validation run was added. Not audited; the capture's performance run is
  identical.
- **`load0-partial/`:** an earlier load from the runner's first form, which
  held its ssh session open. It was cut off at 3,666 programs.
  - All 3,666 ended as simulated, compared again with the reference.
  - It is not counted.

**The order of the loads:** load 1, CoreMark 1 (performance only), load 2,
CoreMark 2 (performance only), then CoreMark's two loads of both runs. Each is a
separate programming of the FPGA. The full runs are detached on the board
(`setsid`) and polled; `--resume` picked each up after its poller's session
limit.

The audit (`scripts/audit_phase20.py`) re-derives all of this from the raw
files here and the bundle:
- the board's reports against the bundle's archived consoles, the captured
  tohost cycles and the reference;
- the final image's hash;
- two separate loads;
- CoreMark's oracle, re-run.
