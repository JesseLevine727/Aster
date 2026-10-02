"""The Phase 18 lockstep comparator matches and rejects synthetic traces."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lockstep
import run_core_tests

ENTRY, TOHOST = 0x80000000, 0x80001040

SPIKE = """core   0: 3 0x00001000 (0x00000297) x5  0x00001000
core   0: 3 0x80000000 (0x00500513) x10 0x00000005
core   0: 3 0x80000004 (0x00a2a023) mem 0x80001000 0x00000005
core   0: 3 0x80000008 (0x0012c683) x13 0x00000000 mem 0x80001001
core   0: 3 0x8000000c (0x00a28223) mem 0x80001004 0x05
core   0: 3 0x80000010 (0x00b5a52f) x10 0x00000005 mem 0x80001000 mem 0x80001000 0x0000000a
core   0: 3 0x80000014 (0x04afa023) mem 0x80001040 0x00000001
core   0: 3 0x80000018 (0x0000006f)
""".splitlines()

# order pc insn trap rd rd_wdata addr rmask wmask rdata wdata (exact byte masks)
TRACE = """0 80000000 00500513 0 10 00000005 00000000 0 0 00000000 00000000
1 80000004 00a2a023 0 0 00000000 80001000 0 f 00000000 00000005
2 80000008 0012c683 0 13 00000000 80001000 2 0 00000500 00000000
3 8000000c 00a28223 0 0 00000000 80001004 0 1 00000000 00000005
4 80000010 00b5a52f 0 10 00000005 80001000 f f 00000005 0000000a
5 80000014 04afa023 0 0 00000000 80001040 0 f 00000000 00000001
""".splitlines()


def replace(index, line, trace=TRACE):
    return trace[:index] + [line] + trace[index + 1:]


class Lockstep(unittest.TestCase):
    def setUp(self):
        self.spike = lockstep.parse_spike(SPIKE, ENTRY, TOHOST)
        self.dut = lockstep.parse_trace(TRACE)

    def compare(self, trace, **kwargs):
        return lockstep.compare(lockstep.parse_trace(trace), self.spike, **kwargs)

    def test_boot_rom_is_skipped_and_stream_ends_at_tohost(self):
        self.assertEqual(self.spike[0].pc, ENTRY)
        self.assertEqual(self.spike[-1].mem, (TOHOST, 4))
        self.assertEqual(len(self.spike), 6)
        ok, message = lockstep.compare(self.dut, self.spike)
        self.assertTrue(ok, message)

    def test_byte_lanes_normalize_to_byte_address_and_size(self):
        self.assertEqual(self.dut[2].mem, (0x80001001, 1))   # lbu with rmask 0b0010
        self.assertEqual(self.dut[3].mem, (0x80001004, 1))
        self.assertEqual(self.dut[3].store, 0x05)

    def test_amo_compares_its_store_side(self):
        self.assertEqual(self.spike[4].mem, (0x80001000, 4))
        self.assertEqual(self.spike[4].store, 0x0000000a)
        self.assertEqual(self.spike[4].rd, (10, 5))
        ok, message = self.compare(replace(4, "4 80000010 00b5a52f 0 10 00000005 80001000 f f 00000005 0000000b"))
        self.assertFalse(ok)
        self.assertIn("store", message.splitlines()[0])

    def test_each_field_mismatch_is_reported(self):
        cases = {
            "pc": "0 80000004 00500513 0 10 00000005 00000000 0 0 00000000 00000000",
            "insn": "0 80000000 00500593 0 10 00000005 00000000 0 0 00000000 00000000",
            "rd": "0 80000000 00500513 0 11 00000005 00000000 0 0 00000000 00000000",
            "trap": "0 80000000 00500513 1 0 00000000 00000000 0 0 00000000 00000000 "
                    "c341=80000000 c342=00000002 c343=00500513",
        }
        for field, line in cases.items():
            with self.subTest(field=field):
                ok, message = self.compare(replace(0, line))
                self.assertFalse(ok)
                self.assertIn(field, message.splitlines()[0])
        ok, message = self.compare(replace(1, "1 80000004 00a2a023 0 0 00000000 80001000 0 f 00000000 00000006"))
        self.assertFalse(ok)
        self.assertIn("store", message.splitlines()[0])

    def test_store_to_the_wrong_byte_lane_fails(self):
        ok, message = self.compare(replace(3, "3 8000000c 00a28223 0 0 00000000 80001004 0 2 00000000 00000500"))
        self.assertFalse(ok)
        self.assertIn("mem", message.splitlines()[0])

    def test_masks_must_be_one_aligned_access(self):
        for mask in ("5", "9", "6", "7", "e"):
            with self.subTest(mask=mask):
                with self.assertRaisesRegex(ValueError, "naturally aligned"):
                    lockstep.parse_trace([f"0 80000004 00a2a023 0 0 00000000 80001000 0 {mask} 00000000 00000000"])

    def test_byte_address_must_agree_with_mask(self):
        lockstep.parse_trace(["0 8000000c 00a28223 0 0 00000000 80001005 0 2 00000000 00000500"])
        with self.assertRaisesRegex(ValueError, "disagrees"):
            lockstep.parse_trace(["0 8000000c 00a28223 0 0 00000000 80001006 0 2 00000000 00000500"])

    def test_x0_write_value_and_store_with_spurious_read_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "x0"):
            lockstep.parse_trace(["0 80000000 00500013 0 0 00000005 00000000 0 0 00000000 00000000"])
        with self.assertRaisesRegex(ValueError, "only an AMO"):
            lockstep.parse_trace(["0 80000004 00a29023 0 0 00000000 80001000 3 c 00000000 00050000"])
        with self.assertRaisesRegex(ValueError, "only an AMO"):   # a plain sh claiming an equal read
            lockstep.parse_trace(["0 80000004 00a29023 0 0 00000000 80001000 c c 00000000 00050000"])

    def test_dropped_or_duplicated_record_is_rejected_by_order(self):
        with self.assertRaisesRegex(ValueError, "order"):
            lockstep.parse_trace(TRACE[:2] + TRACE[3:])
        with self.assertRaisesRegex(ValueError, "order"):
            lockstep.parse_trace(TRACE[:3] + TRACE[2:])

    def test_truncated_trace_fails(self):
        for length in (1, 3, len(TRACE) - 1):
            with self.subTest(length=length):
                ok, message = self.compare(TRACE[:length])
                self.assertFalse(ok)
                self.assertIn(f"ended after {length} records", message)

    def test_dut_longer_than_spike_fails(self):
        extra = TRACE + ["6 80000018 0000006f 0 0 00000000 00000000 0 0 00000000 00000000"]
        ok, message = self.compare(extra)
        self.assertFalse(ok)
        self.assertIn("after Spike's tohost store", message)

    def test_spike_log_without_tohost_store_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no store to tohost"):
            lockstep.parse_spike(SPIKE[:6], ENTRY, TOHOST)
        with self.assertRaisesRegex(ValueError, "never reached the entry"):
            lockstep.parse_spike(SPIKE[:1], ENTRY, TOHOST)

    def test_word_granular_loads_ignore_only_the_byte_offset(self):
        word = replace(2, "2 80000008 0012c683 0 13 00000000 80001000 f 0 00000500 00000000")
        self.assertFalse(self.compare(word)[0])
        self.assertTrue(self.compare(word, word_loads=True)[0])
        wrong_word = replace(2, "2 80000008 0012c683 0 13 00000000 80001004 f 0 00000500 00000000")
        self.assertFalse(self.compare(wrong_word, word_loads=True)[0])

    def test_malformed_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            lockstep.parse_trace(["0 80000000 00500513"])
        with self.assertRaisesRegex(ValueError, "malformed trace token"):
            lockstep.parse_trace(["0 80000000 00500513 0 10 00000005 00000000 0 0 00000000 00000000 c3x0=1"])

    def test_a_trap_record_writes_nothing_and_reports_the_trap_csrs(self):
        with self.assertRaisesRegex(ValueError, "trap record"):    # a register write
            lockstep.parse_trace(["0 80000000 00500513 1 10 00000005 00000000 0 0 00000000 00000000 "
                                  "c341=80000000 c342=00000002 c343=00000000"])
        with self.assertRaisesRegex(ValueError, "trap record"):    # no mtval
            lockstep.parse_trace(["0 80000000 00500513 1 0 00000000 00000000 0 0 00000000 00000000 "
                                  "c341=80000000 c342=00000002"])


class HazardCoverage(unittest.TestCase):
    @staticmethod
    def record(insn, rd=None):
        return lockstep.Retired(0x80000000, insn, (rd, 0) if rd else None, None, None)

    def test_distances_and_classes(self):
        records = [
            self.record(0x00100093, rd=1),   # addi x1, x0, 1        (alu producer)
            self.record(0x0000a103, rd=2),   # lw   x2, 0(x1)        (alu -> load-addr, d=1)
            self.record(0x00208233, rd=4),   # add  x4, x1, x2       (alu d=2, load d=1)
            self.record(0x0020a023),         # sw   x2, 0(x1)        (store-addr alu d=3; store-data load d=2)
            self.record(0x02208333, rd=6),   # mul  x6, x1, x2       (muldiv: x1 alu d=4, x2 load d=3)
            self.record(0x00030463),         # beq  x6, x0, +8       (mul -> branch d=1)
            self.record(0x000083b3, rd=7),   # add  x7, x1, x0       (x1 d=6: beyond distance 4, ignored)
        ]
        bins = run_core_tests.hazard_coverage(records)
        self.assertEqual(bins, {("alu", 1, "load-addr"): 1, ("alu", 2, "alu"): 1, ("load", 1, "alu"): 1,
                                ("alu", 3, "store-addr"): 1, ("load", 2, "store-data"): 1,
                                ("alu", 4, "muldiv"): 1, ("load", 3, "muldiv"): 1, ("mul", 1, "branch"): 1})

    def test_atomic_results_are_their_own_producer_class(self):
        # amoadd.w x5, x2, (x1) then add x6, x5, x5: an AMO result is an atomic
        # producer (read through both operands: two pairs)
        records = [self.record(0x002082af, rd=5), self.record(0x00528333, rd=6)]
        self.assertEqual(run_core_tests.hazard_coverage(records), {("atomic", 1, "alu"): 2})

    def test_required_bins_depend_on_extensions(self):
        self.assertEqual(len(run_core_tests.required_bins("")), 4 * (3 * 3 + 3 + 3 + 2))
        self.assertIn(("load", 4, "store-data"), run_core_tests.required_bins(""))
        self.assertTrue(all(p not in ("mul", "div") for p, _, _ in run_core_tests.required_bins("")))
        self.assertIn(("div", 3, "muldiv"), run_core_tests.required_bins("m"))

    def test_required_bins_with_atomics(self):
        # 18.4: AMO data from every producer, AMO addresses from ALU, load and
        # AMO results, and AMO results to every data consumer and as addresses
        bins = run_core_tests.required_bins("m,zicsr,a")
        self.assertEqual(len(bins), 220)
        self.assertIn(("mul", 1, "amo-data"), bins)
        self.assertIn(("load", 1, "amo-addr"), bins)
        self.assertIn(("atomic", 1, "amo-addr"), bins)
        self.assertIn(("atomic", 2, "csr"), bins)
        self.assertNotIn(("atomic", 1, "jalr"), bins)
        self.assertNotIn(("link", 1, "amo-addr"), bins)
        self.assertEqual(len(run_core_tests.required_bins("m,zicsr")), 152)

    def test_amo_operands_and_lr_address_are_consumers(self):
        records = [
            self.record(0x00100093, rd=1),   # addi x1, x0, 1
            self.record(0x00200113, rd=2),   # addi x2, x0, 2
            self.record(0x002082af, rd=5),   # amoadd.w x5, x2, (x1): x1 d=2 address, x2 d=1 data
            self.record(0x1000a2af, rd=5),   # lr.w x5, (x1): x1 d=3 address (rs2 is not read)
            self.record(0x1820a32f, rd=6),   # sc.w x6, x2, (x1): x1 d=4, x2 d=3
        ]
        self.assertEqual(run_core_tests.hazard_coverage(records),
                         {("alu", 2, "amo-addr"): 1, ("alu", 1, "amo-data"): 1, ("alu", 3, "amo-addr"): 1,
                          ("alu", 4, "amo-addr"): 1, ("alu", 3, "amo-data"): 1})


class ScOutcomes(unittest.TestCase):
    """The runner hands the shell Spike's sc outcomes in program order (18.4)."""

    SPIKE = """core   0: 3 0x00001000 (0x00000297) x5  0x00001000
core   0: 0x80000000 (0x1000a2af) lr.w    t0, (ra)
core   0: 3 0x80000000 (0x1000a2af) x5  0x00000007 mem 0x80001000
core   0: 0x80000004 (0x1820a32f) sc.w    t1, sp, (ra)
core   0: 3 0x80000004 (0x1820a32f) x6  0x00000000 mem 0x80001000 0x00000002
core   0: 0x80000008 (0x1820a32f) sc.w    t1, sp, (ra)
core   0: 3 0x80000008 (0x1820a32f) x6  0x00000001
core   0: 0x8000000c (0x1820a32f) sc.w    t1, sp, (ra)
core   0: exception trap_store_address_misaligned, epc 0x8000000c
core   0:           tval 0x80001001
core   0: 0x80000010 (0x002082af) amoadd.w t0, sp, (ra)
core   0: 3 0x80000010 (0x002082af) x5  0x00000002 mem 0x80001000 mem 0x80001000 0x00000004
core   0: 0x80000014 (0x04afa023) sw      a0, 64(t6)
core   0: 3 0x80000014 (0x04afa023) mem 0x80001040 0x00000001
"""

    def test_successes_and_failures_in_order_skipping_traps_and_amos(self):
        self.assertEqual(run_core_tests.sc_outcomes(self.SPIKE, TOHOST), "SF")

    def test_unparsable_log_gives_no_outcomes(self):
        self.assertEqual(run_core_tests.sc_outcomes(self.SPIKE.splitlines()[0], TOHOST), "")


class SignatureCheck(unittest.TestCase):
    def check(self, dut, spike):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            elf = Path(temp) / "t.elf"
            if dut is not None:
                elf.with_suffix(".sig.dut").write_text(dut)
            if spike is not None:
                elf.with_suffix(".sig.spike").write_text(spike)
            return run_core_tests.signature_check(elf)

    def test_equal_signatures_pass(self):
        ok, message = self.check("00000001\ndeadbeef\n", "00000001\ndeadbeef\n")
        self.assertTrue(ok, message)

    def test_differing_word_length_or_missing_file_fails(self):
        self.assertIn("word 1 differs", self.check("00000001\ndeadbeef\n", "00000001\ndeadbeee\n")[1])
        self.assertIn("lengths differ", self.check("00000001\n", "00000001\ndeadbeef\n")[1])
        self.assertIn("lengths differ", self.check("00000001\ndeadbeef\n", "00000001\n")[1])
        self.assertIn("not written", self.check(None, "00000001\n")[1])
        self.assertIn("empty", self.check("", "")[1])


class KernelEndRule(unittest.TestCase):
    """lockstep.KernelEnd must end where the shells' observer ends a kernel run."""

    @staticmethod
    def store(address, value, size=4):
        return lockstep.Retired(0x80000000, 0, None, (address, size), value)

    def console(self, text):
        return [self.store(lockstep.CONSOLE, ord(c), 1) for c in text]

    def ends(self, records):
        end = lockstep.KernelEnd()
        return [index for index, r in enumerate(records) if end(r)]

    def test_ends_at_the_newline_of_the_first_record_line_after_the_window(self):
        records = (self.console("Aster boot\n") + [self.store(0x20003038, 1), self.store(0x20003038, 2)]
                   + self.console("note\nASTERBENCH,status=PASS\nAgain\n"))
        # the run ends at the first such newline (a later 'A' line would match again)
        self.assertEqual(self.ends(records)[0], len(records) - 7)

    def test_no_end_before_the_window_closes_or_without_a_word_store(self):
        self.assertEqual(self.ends(self.console("ASTERBENCH\n")), [])
        opened = [self.store(0x20003080, 1), self.store(0x20003080, 2, 2)] + self.console("ASTERBENCH\n")
        self.assertEqual(self.ends(opened), [])
        closed = [self.store(0x20003080, 1), self.store(0x20003080, 2)] + self.console("ASTERBENCH\n")
        self.assertEqual(self.ends(closed), [len(closed) - 1])

    def test_console_stores_count_only_at_the_console_byte(self):
        records = ([self.store(0x20003038, 1), self.store(0x20003038, 2), self.store(lockstep.CONSOLE + 1, ord("A"), 1)]
                   + self.console("B\n") + [self.store(lockstep.CONSOLE + 1, ord("\n"), 1)])
        self.assertEqual(self.ends(records), [])

    def test_parse_spike_without_the_end_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no kernel end"):
            lockstep.parse_spike(SPIKE[:4], ENTRY, TOHOST, end=lockstep.KernelEnd())


class TrapsAndCsrs(unittest.TestCase):
    """18.3: Spike's -l exception lines as trap records, CSR writes compared,
    and the allowlists of docs/cpu.md §6."""

    SPIKE = """core   0: 3 0x00001000 (0x00000297) x5  0x00001000
core   0: 0x80000000 (0x30529073) csrw    mtvec, t0
core   0: 3 0x80000000 (0x30529073) c773_mtvec 0x80000100
core   0: 0x80000004 (0x00000073) ecall
core   0: exception trap_machine_ecall, epc 0x80000004
core   0: >>>>  handler
core   0: 0x80000100 (0x34202573) csrr    a0, mcause
core   0: 3 0x80000100 (0x34202573) x10 0x0000000b
core   0: 0x80000104 (0xb0002673) csrr    a2, mcycle
core   0: 3 0x80000104 (0xb0002673) x12 0x00001234
core   0: 0x80000108 (0x00102383) lw      t2, 1(zero)
core   0: exception trap_load_address_misaligned, epc 0x80000108
core   0:           tval 0x00000001
core   0: exception trap_instruction_access_fault, epc 0x40000000
core   0:           tval 0x40000000
core   0: 0x8000010c (0x34431073) csrw    mip, t1
core   0: 3 0x8000010c (0x34431073) c836_mip 0x00000080
core   0: 0x80000110 (0x30200073) mret
core   0: 3 0x80000110 (0x30200073) c768_mstatus 0x00001880 c784_mstatush 0x00000000 c1957_tcontrol 0x00000000
core   0: 3 0x80000114 (0x04afa023) mem 0x80001040 0x00000001
""".splitlines()
    TRACE = """0 80000000 30529073 0 0 00000000 00000000 0 0 00000000 00000000 c305=80000100
1 80000004 00000073 1 0 00000000 00000000 0 0 00000000 00000000 c300=00001800 c341=80000004 c342=0000000b c343=00000000
2 80000100 34202573 0 10 0000000b 00000000 0 0 00000000 00000000 intr
3 80000104 b0002673 0 12 00009999 00000000 0 0 00000000 00000000
4 80000108 00102383 1 0 00000000 00000000 0 0 00000000 00000000 c300=00001800 c341=80000108 c342=00000004 c343=00000001
5 40000000 5a5a5a5a 1 0 00000000 00000000 0 0 00000000 00000000 c300=00001800 c341=40000000 c342=00000001 c343=40000000 intr
6 8000010c 34431073 0 0 00000000 00000000 0 0 00000000 00000000 c344=00000000 intr
7 80000110 30200073 0 0 00000000 00000000 0 0 00000000 00000000 c300=00001880 c310=00000000
8 80000114 04afa023 0 0 00000000 80001040 0 f 00000000 00000001
""".splitlines()

    def check(self, trace=TRACE, spike=SPIKE):
        return lockstep.compare(lockstep.parse_trace(trace), lockstep.parse_spike(spike, ENTRY, TOHOST))

    def test_spike_exceptions_become_trap_records(self):
        records = lockstep.parse_spike(self.SPIKE, ENTRY, TOHOST)
        self.assertEqual(len(records), 9)
        ecall, misaligned, fault = records[1], records[4], records[5]
        self.assertEqual((ecall.trap, ecall.insn, dict(ecall.csrs)),
                         (True, 0x73, {lockstep.MEPC: 0x80000004, lockstep.MCAUSE: 11, lockstep.MTVAL: 0}))
        self.assertEqual(dict(misaligned.csrs)[lockstep.MTVAL], 1)
        self.assertIsNone(fault.insn)                  # a fetch fault fetched no instruction
        self.assertEqual(dict(records[7].csrs), {0x300: 0x1880, 0x310: 0})     # tcontrol left out

    def test_spike_interrupts_become_trap_records(self):
        log = ["core   0: 3 0x80000000 (0x00500513) x10 0x00000005",
               "core   0: exception interrupt #11, epc 0x80000004",
               "core   0: 3 0x80000100 (0x04afa023) mem 0x80001040 0x00000001"]
        records = lockstep.parse_spike(log, ENTRY, TOHOST)
        self.assertEqual((records[1].trap, records[1].insn, dict(records[1].csrs)[lockstep.MCAUSE]),
                         (True, None, (1 << 31) | 11))

    def test_matching_run_passes_with_the_allowlisted_values(self):
        ok, message = self.check()               # mcycle read and mip write values differ, by name
        self.assertTrue(ok, message)

    def test_trap_and_csr_differences_fail(self):
        cases = {
            "cause": (1, "c342=0000000b", "c342=00000003"),
            "tval": (4, "c343=00000001", "c343=00000002"),
            "epc": (4, "c341=80000108", "c341=8000010c"),
            "csr value": (0, "c305=80000100", "c305=80000104"),
            "csr missing": (7, " c310=00000000", ""),
            "read value": (2, "0000000b 00000000", "0000000a 00000000"),
        }
        for name, (index, old, new) in cases.items():
            with self.subTest(case=name):
                trace = list(self.TRACE)
                trace[index] = trace[index].replace(old, new, 1)
                self.assertFalse(self.check(trace=trace)[0])

    def test_intr_marks_exactly_the_records_after_a_trap(self):
        trace = list(self.TRACE)
        trace[3] += " intr"                       # not after a trap
        self.assertIn("rvfi_intr", self.check(trace=trace)[1])
        trace = list(self.TRACE)
        trace[2] = trace[2].removesuffix(" intr")    # after the ecall's trap record
        self.assertIn("rvfi_intr", self.check(trace=trace)[1])

    def test_a_dropped_or_extra_trap_fails(self):
        self.assertFalse(self.check(trace=run_core_tests._renumber(self.TRACE[:1] + self.TRACE[2:]))[0])
        self.assertFalse(self.check(trace=run_core_tests._renumber(self.TRACE[:2] + self.TRACE[1:]))[0])

    def test_an_allowlisted_csr_is_left_out_only_by_name(self):
        record = lockstep.Retired(0x80000000, 0x344025f3, (11, 0x80), None, None, csrs=((0x344, 0x80),))
        self.assertEqual(lockstep.normalized(record).rd, (11, None))
        self.assertEqual(lockstep.normalized(record).csrs, ((0x344, None),))
        mstatus = lockstep.Retired(0x80000000, 0x300025f3, (11, 0x1800), None, None)    # csrr a1, mstatus
        self.assertEqual(lockstep.normalized(mstatus).rd, (11, 0x1800))


class InterruptSplice(unittest.TestCase):
    """run_core_tests.splice: each interrupt handler cut out of the DUT's stream."""

    @staticmethod
    def record(pc, insn, trap=False, intr=False):
        return lockstep.Retired(pc, insn, None, None, None, trap, intr=intr)

    def test_handlers_are_cut_and_pairs_classified(self):
        stream = [self.record(0x80000000, 0x00000013),                  # addi (interrupted after)
                  self.record(0x80000100, 0x00000013, intr=True),       # handler
                  self.record(0x80000104, 0x30200073),                  # mret
                  self.record(0x80000004, 0x00002283),                  # lw (killed, re-executed)
                  self.record(0x80000008, 0x00000073, trap=True),       # an exception: kept
                  self.record(0x80000200, 0x00000013, intr=True),       # its handler: kept
                  self.record(0x80000204, 0x30200073)]
        main, pairs, error = run_core_tests.splice(stream)
        self.assertEqual(error, "")
        self.assertEqual([r.pc for r in main], [0x80000000, 0x80000004, 0x80000008, 0x80000200, 0x80000204])
        self.assertEqual(pairs, [("alu", "load")])

    def test_a_handler_that_never_returns_fails(self):
        stream = [self.record(0x80000000, 0x00000013), self.record(0x80000100, 0x00000013, intr=True)]
        self.assertIn("never returns", run_core_tests.splice(stream)[2])


class ArchCase(unittest.TestCase):
    """run_core_tests.arch_case: the RVTEST_CASE that applies to the DUT, as riscof selects it."""

    def case(self, text, dut):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "t.S"
            path.write_text(text)
            return run_core_tests.arch_case(path, run_core_tests.DUTS[dut])

    def test_the_case_for_the_dut_is_chosen(self):
        text = ('RVTEST_CASE(0,"//check ISA:=regex(.*32.*);check ISA:=regex(.*I.*C.*); def X=True;def TEST_CASE_1=True;",t)\n'
                'RVTEST_CASE(1,"//check ISA:=regex(.*32.*);check ISA:=regex(.*I.*Zicsr.*); '
                'check hw_data_misaligned_support:=False; def rvtest_mtrap_routine=True;def TEST_CASE_1=True;",t)\n')
        self.assertEqual(self.case(text, "aster"), ["rvtest_mtrap_routine=True", "TEST_CASE_1=True"])
        self.assertIsNone(self.case(text, "picorv32"))       # no Zicsr, no C
        plain = 'RVTEST_CASE(0,"//check ISA:=regex(.*32.*);check ISA:=regex(.*I.*M.*);def TEST_CASE_1=True;",mul)'
        self.assertEqual(self.case(plain, "picorv32"), ["TEST_CASE_1=True"])


class RandomGenerator(unittest.TestCase):
    """rvgen returns every register it reserves for a sequence to the pool."""

    def test_no_register_stays_reserved(self):
        import rvgen
        for ext in ("m", "m,zicsr", "m,a,zicsr,zifencei", "m,a,irqcsr,zifencei"):
            for seed in range(1, 6):
                generator = rvgen.Generator(seed, ext)
                generator.program(4000)
                self.assertEqual(generator.pinned, set(), f"seed {seed}, --ext {ext}")


if __name__ == "__main__":
    unittest.main()
