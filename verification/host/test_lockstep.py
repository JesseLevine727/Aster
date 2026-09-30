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
            "trap": "0 80000000 00500513 1 10 00000005 00000000 0 0 00000000 00000000",
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

    def test_amos_produce_like_loads(self):
        # amoadd.w x5, x2, (x1) then add x6, x5, x5: an AMO result is a load-class
        # producer (read through both operands: two pairs)
        records = [self.record(0x002082af, rd=5), self.record(0x00528333, rd=6)]
        self.assertEqual(run_core_tests.hazard_coverage(records), {("load", 1, "alu"): 2})

    def test_required_bins_depend_on_extensions(self):
        self.assertEqual(len(run_core_tests.required_bins("")), 4 * (3 * 3 + 3 + 3 + 2))
        self.assertIn(("load", 4, "store-data"), run_core_tests.required_bins(""))
        self.assertTrue(all(p not in ("mul", "div") for p, _, _ in run_core_tests.required_bins("")))
        self.assertIn(("div", 3, "muldiv"), run_core_tests.required_bins("m"))


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


class TrapCheck(unittest.TestCase):
    """run_core_tests.trap_check: trap record at trap_pc, lockstep before it, and
    Spike must not execute trap_pc."""

    SPIKE = """core   0: 3 0x00001000 (0x00000297) x5  0x00001000
core   0: 3 0x80000000 (0x00500513) x10 0x00000005
core   0: 3 0x80000004 (0x00a00593) x11 0x0000000a
""".splitlines()
    TRACE = """0 80000000 00500513 0 10 00000005 00000000 0 0 00000000 00000000
1 80000004 00a00593 0 11 0000000a 00000000 0 0 00000000 00000000
2 80000008 00000000 1 0 00000000 00000000 0 0 00000000 00000000
""".splitlines()
    SYMBOLS = {"trap_pc": 0x80000008, "tohost": 0x80001000}

    def check(self, trace=TRACE, spike=SPIKE, symbols=None):
        return run_core_tests.trap_check("\n".join(trace), "\n".join(spike), symbols or self.SYMBOLS, False)

    def test_a_trap_at_trap_pc_after_matching_records_passes(self):
        ok, message = self.check()
        self.assertTrue(ok, message)

    def test_a_trap_elsewhere_fails(self):
        ok, message = self.check(symbols={"trap_pc": 0x8000000c, "tohost": 0x80001000})
        self.assertFalse(ok)
        self.assertIn("expected trap_pc", message)

    def test_a_differing_earlier_record_fails(self):
        trace = list(self.TRACE)
        trace[1] = "1 80000004 00a00593 0 11 0000000b 00000000 0 0 00000000 00000000"
        self.assertFalse(self.check(trace=trace)[0])

    def test_spike_executing_trap_pc_fails(self):
        spike = self.SPIKE + ["core   0: 3 0x80000008 (0x00100613) x12 0x00000001"]
        ok, message = self.check(spike=spike)
        self.assertFalse(ok)
        self.assertIn("without trapping", message)

    def test_a_trace_without_a_trap_record_fails(self):
        self.assertFalse(self.check(trace=self.TRACE[:2])[0])


if __name__ == "__main__":
    unittest.main()
