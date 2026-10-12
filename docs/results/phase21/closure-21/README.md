# Phase 21: the 100 MHz closure

The text of record is [`../../../phase21.md`](../../../phase21.md) §2. `SHA256SUMS` covers every file here.

**The closure is of Phase 20's final image.** Its evidence is Phase 20's:
- the final build's own reports: `../../phase20/matrix-20.5/timing/final-build`;
- the board run: `../../phase20/board-20.5`, with every R program on two loads and CoreMark's two runs on two more.

**`checkpoint.txt`:** the routed checkpoint bound to the image. A bitstream written from it has the configuration
data of record (0bf4b4c3…).

**`audit.txt`:** Phase 20's closeout audit (`scripts/audit_phase20.py`), re-run at 65a238d for this record. It says
PASS on every check:
- the evidence's checksums;
- the bundle's commit;
- every planned entry;
- the records, totals, gates and ECG overlap, re-derived from the raw records;
- the final build's timing, routing, DRC, methodology and utilization;
- both board loads against the archived consoles and the board reference;
- CoreMark's two runs on two loads.
