# Milestone 20.5, step 6: the report and the closeout audit

The text of record is the "Step 6" section of 20.5 in
[`../../../phase20.md`](../../../phase20.md). The report is
[`../../../phase20-report.md`](../../../phase20-report.md). `SHA256SUMS` covers
every file here.

- **`tables.md`:** the report's tables, generated from the final capture
  (`build/matrix/f5-*`, b96e48b) by `scripts/phase20_report_data.py
  build/matrix`.
- **`audit.txt`:** `scripts/audit_phase20.py` on the final bundle
  (`../matrix-20.5`) and the board's evidence (`../board-20.5`). It says PASS
  only if every check passes.
- **`audit-self-test.txt`:** `scripts/audit_phase20.py --self-test`.
  - Each check is shown failing on a planted fault, for that fault's own
    reason.
  - Four of the checks (the manifest, timing, board and CoreMark checks) are also
    shown passing on a sound input that the self-test makes.
