"""The trace-driven CPI model applies the cpu.md §4 hazard rules as specified."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import cpi_model
import lockstep

SEVEN, FIVE = cpi_model.SEVEN_STAGE, cpi_model.FIVE_STAGE


def record(pc, insn, rd=None):
    return lockstep.Retired(pc, insn, (rd, 0) if rd else None, None, None)


def straight(*insns, rds=None):
    rds = rds or [None] * len(insns)
    return [record(0x80000000 + 4 * i, insn, rd) for i, (insn, rd) in enumerate(zip(insns, rds))]


ADDI_X1 = 0x00100093       # addi x1, x0, 1
LW_X1 = 0x0000a083         # lw   x1, 0(x1)
ADD_X2_X1 = 0x00108133     # add  x2, x1, x1
NOP = 0x00000013           # addi x0, x0, 0
MUL_X1 = 0x021080b3        # mul  x1, x1, x1
DIV_X1 = 0x0210c0b3        # div  x1, x1, x1
SW_X1 = 0x0010a023         # sw   x1, 0(x1)
AMOADD_X1 = 0x0020a0af     # amoadd.w x1, x2, (x1)
CSRRS_X2_X1 = 0x3000a173   # csrrs x2, mstatus, x1
CSRRSI_X2 = 0x3000e173     # csrrsi x2, mstatus, 1 (the rs1 field is the immediate 1)
ADD_X2_X0 = 0x00000133     # add  x2, x0, x0


class CpiModel(unittest.TestCase):
    def test_independent_instructions_take_one_cycle_each(self):
        self.assertEqual(cpi_model.cycles(straight(NOP, NOP, NOP, NOP), SEVEN), 4)

    def test_load_use_costs_two_cycles_at_distance_one_and_one_at_distance_two(self):
        self.assertEqual(cpi_model.cycles(straight(LW_X1, ADD_X2_X1, rds=[1, 2]), SEVEN), 2 + 2)
        self.assertEqual(cpi_model.cycles(straight(LW_X1, NOP, ADD_X2_X1, rds=[1, None, 2]), SEVEN), 3 + 1)
        self.assertEqual(cpi_model.cycles(straight(LW_X1, ADD_X2_X1, rds=[1, 2]), FIVE), 2 + 1)

    def test_store_data_from_a_load_waits_like_a_load_use(self):
        self.assertEqual(cpi_model.cycles(straight(LW_X1, SW_X1, rds=[1, None]), SEVEN), 2 + 2)

    def test_alu_results_forward_without_stall(self):
        self.assertEqual(cpi_model.cycles(straight(ADDI_X1, ADD_X2_X1, rds=[1, 2]), SEVEN), 2)

    def test_multiply_and_divide(self):
        self.assertEqual(cpi_model.cycles(straight(MUL_X1, ADD_X2_X1, rds=[1, 2]), SEVEN), 2 + 2)
        self.assertEqual(cpi_model.cycles(straight(DIV_X1, NOP, rds=[1, None]), SEVEN), cpi_model.DIVIDE_CYCLES + 1)

    def test_redirect_penalties(self):
        jal = [record(0x80000000, 0x0080006f), record(0x80000008, NOP)]            # jal x0, +8
        self.assertEqual(cpi_model.cycles(jal, SEVEN), 2 + 2)
        # beq x0, x0, -4 (backward, predicted taken) taken: a Decode redirect
        back = [record(0x80000004, 0xfe000ee3), record(0x80000000, NOP)]
        self.assertEqual(cpi_model.cycles(back, SEVEN), 2 + 2)
        # beq x0, x0, +8 (forward, predicted not taken) taken: mispredicted, an Execute redirect
        fwd = [record(0x80000000, 0x00000463), record(0x80000008, NOP)]
        self.assertEqual(cpi_model.cycles(fwd, SEVEN), 2 + 4)
        self.assertEqual(cpi_model.cycles(fwd, FIVE), 2 + 2)
        # forward branch not taken: predicted correctly, no penalty
        self.assertEqual(cpi_model.cycles([record(0x80000000, 0x00000463), record(0x80000004, NOP)], SEVEN), 2)

    def test_jalr_redirects_from_execute_and_backward_not_taken_is_mispredicted(self):
        jalr = [record(0x80000000, 0x00008067), record(0x80000100, NOP)]            # jalr x0, 0(x1)
        self.assertEqual(cpi_model.cycles(jalr, SEVEN), 2 + 4)
        # beq x0, x0, -4 not taken: predicted taken, an Execute redirect
        back = [record(0x80000004, 0xfe000ee3), record(0x80000008, NOP)]
        self.assertEqual(cpi_model.cycles(back, SEVEN), 2 + 4)

    def test_amo_results_wait_like_loads(self):
        self.assertEqual(cpi_model.cycles(straight(AMOADD_X1, ADD_X2_X1, rds=[1, 2]), SEVEN), 2 + 2)

    def test_x0_is_never_waited_on(self):
        load_x0 = lockstep.Retired(0x80000000, 0x0000a003, (0, 0), None, None)   # lw x0, 0(x1)
        self.assertEqual(cpi_model.cycles([load_x0, record(0x80000004, ADD_X2_X0, 2)], SEVEN), 2)

    def test_only_register_csr_forms_read_rs1(self):
        self.assertEqual(cpi_model.cycles(straight(LW_X1, CSRRS_X2_X1, rds=[1, 2]), SEVEN), 2 + 2)
        self.assertEqual(cpi_model.cycles(straight(LW_X1, CSRRSI_X2, rds=[1, 2]), SEVEN), 2)

    def test_a_decode_redirect_overlaps_the_wait_for_its_operands(self):
        # lw x1; bne x1, x0, -4 (backward, taken): the branch redirects from its first
        # Decode cycle while it waits two cycles for the load, as the RTL does.
        lw = lockstep.Retired(0x80000000, LW_X1, (1, 0), (0x80001000, 4), None)
        bne = record(0x80000004, 0xfe009ee3)
        self.assertEqual(cpi_model.cycles([lw, bne, record(0x80000000, NOP)], SEVEN), 5)

    def test_a_backward_branch_to_a_halfword_target_is_not_predicted(self):
        # bne x0, x0, -6: backward, but its target is not word-aligned; not taken, no penalty
        self.assertEqual(cpi_model.cycles([record(0x80000008, 0xfe001de3), record(0x8000000c, NOP)], SEVEN), 2)

    def test_a_branch_to_its_fall_through_never_redirects(self):
        # beq x0, x0, +4: taken, but the next PC is the fall-through either way.
        self.assertEqual(cpi_model.cycles([record(0x80000000, 0x00000263), record(0x80000004, NOP)], SEVEN), 2)

    def test_window_is_after_the_opening_store_through_the_closing_one(self):
        def store(value):
            return lockstep.Retired(0x80000000, 0x00f02023, None, (0x20003038, 4), value)
        records = [record(0, NOP), store(1), record(0, NOP), record(0, NOP), store(2), record(0, NOP)]
        self.assertEqual(len(cpi_model.window(records)), 3)
        with self.assertRaisesRegex(ValueError, "no measurement window"):
            cpi_model.window(records[:3])


if __name__ == "__main__":
    unittest.main()
