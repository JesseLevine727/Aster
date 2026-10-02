// Aster core decoder, ALU and branch compare (docs/cpu.md §2, §4). Milestones
// 18.1-18.4 implement RV32IMA, Zicsr, Zifencei, `ecall`, `ebreak`, `mret` and
// `wfi` (a no-op), with the machine-mode CSRs of docs/cpu.md §3; every other
// encoding — the rest of SYSTEM, custom-0 until 18.5 — decodes as illegal.
// `fence` executes as a no-op: the core's accesses take effect in order at one
// memory (docs/cpu.md §5). `fence.i` (any encoding with funct3 1, as Spike
// decodes it) drains the data accesses in flight and refetches what follows.
//
// Atomics: `lr.w`, `sc.w` and the nine AMOs go to the data port as one
// operation each (aq/rl are implied: the core's accesses are in order), coded
// for d_req_op as AMO_*; their result comes from the memory, as a load's does.
`timescale 1 ns / 1 ps
package aster_core_pkg;
    // Execute's result, one-hot: decoded in Decode and registered, so Execute's
    // result is an AND-OR of its sources with no decode after them.
    typedef struct packed {
        logic add;                    // rs1 + operand B
        logic sub;                    // rs1 - rs2
        logic sll, slt, sltu, bxor, srl, sra, bor, band;
        logic imm;                    // the immediate (lui)
        logic pc_imm;                 // the PC plus the immediate (auipc), from the branch-target adder
        logic link;                   // the PC plus 4 (jal, jalr)
        logic div;                    // the divider's result
        logic csr;                    // the CSR's value as read
    } result_t;

    // The CSR a Zicsr instruction names, one-hot (docs/cpu.md §3), decoded in
    // Decode so that Execute's read is an AND-OR of the registers. The
    // user-level counters read the machine counters, and time/timeh the core's
    // free-running time counter; `zero` is mvendorid,
    // marchid, mimpid and mconfigptr (read-only), and the hardware performance
    // monitor's mhpmcounter3-31(h) and mhpmevent3-31, implemented as zero
    // (writes are legal and change nothing).
    typedef struct packed {
        logic mstatus, misa, mie, mtvec, mstatush, mcountinhibit;
        logic mscratch, mepc, mcause, mtval, mip;
        logic mcycle, mcycleh, minstret, minstreth;
        logic time_lo, time_hi;       // time, timeh: the free-running time counter (read-only)
        logic zero, mhartid;
    } csr_t;

    // The CSR at `address`, or none (an unimplemented CSR is an illegal instruction).
    function automatic csr_t csr_decode(input logic [11:0] address);
        csr_t c;
        c = '0;
        if ((address >= 12'hB03 && address <= 12'hB1F) || (address >= 12'hB83 && address <= 12'hB9F) ||
            (address >= 12'h323 && address <= 12'h33F)) c.zero = 1'b1;
        else unique case (address)
            12'h300: c.mstatus = 1'b1;
            12'h301: c.misa = 1'b1;
            12'h304: c.mie = 1'b1;
            12'h305: c.mtvec = 1'b1;
            12'h310: c.mstatush = 1'b1;
            12'h320: c.mcountinhibit = 1'b1;
            12'h340: c.mscratch = 1'b1;
            12'h341: c.mepc = 1'b1;
            12'h342: c.mcause = 1'b1;
            12'h343: c.mtval = 1'b1;
            12'h344: c.mip = 1'b1;
            12'hB00, 12'hC00: c.mcycle = 1'b1;
            12'hC01: c.time_lo = 1'b1;
            12'hC81: c.time_hi = 1'b1;
            12'hB80, 12'hC80: c.mcycleh = 1'b1;
            12'hB02, 12'hC02: c.minstret = 1'b1;
            12'hB82, 12'hC82: c.minstreth = 1'b1;
            12'hF11, 12'hF12, 12'hF13, 12'hF15: c.zero = 1'b1;
            12'hF14: c.mhartid = 1'b1;
            default: ;
        endcase
        return c;
    endfunction

    // d_req_op codes (docs/cpu.md §5).
    localparam logic [3:0] OP_LOAD = 4'd0, OP_STORE = 4'd1, OP_LR = 4'd2, OP_SC = 4'd3,
                           OP_SWAP = 4'd4, OP_ADD = 4'd5, OP_XOR = 4'd6, OP_AND = 4'd7, OP_OR = 4'd8,
                           OP_MIN = 4'd9, OP_MAX = 4'd10, OP_MINU = 4'd11, OP_MAXU = 4'd12;

    typedef struct packed {
        logic        illegal;
        logic        uses_rs1;
        logic        uses_rs2;
        logic        writes_rd;       // and rd is not x0
        logic [4:0]  rs1;
        logic [4:0]  rs2;
        logic [4:0]  rd;
        logic [31:0] imm;
        result_t     res;             // one-hot
        logic        b_imm;           // operand B is the immediate
        logic        load;
        logic        store;
        logic        branch;
        logic        jal;
        logic        jalr;
        logic        mul;             // mul, mulh, mulhsu, mulhu (funct3 0-3)
        logic        div;             // div, divu, rem, remu (funct3 4-7)
        logic        csr;             // a Zicsr instruction (it reads the CSR; imm holds a csrr*i's uimm)
        logic        csr_write;       // and it writes the CSR (csrrw/csrrwi, or a set/clear with a nonzero source field)
        csr_t        csr_sel;
        logic        ecall;
        logic        ebreak;
        logic        mret;
        logic        sys;             // csr or mret: Execute holds it until M1 is empty
        logic        atomic;          // lr.w, sc.w or an AMO (also load; sc and AMOs also store)
        logic [3:0]  amo_op;          // its d_req_op
        logic        fencei;          // fence.i: Execute holds it until M1 and M2 are empty
        logic [2:0]  funct3;
    } decoded_t;

    function automatic decoded_t decode(input logic [31:0] insn);
        decoded_t d;
        logic [31:0] imm_i, imm_s, imm_b, imm_u, imm_j;
        logic [2:0] funct3;
        logic [6:0] funct7;
        imm_i  = {{20{insn[31]}}, insn[31:20]};
        imm_s  = {{20{insn[31]}}, insn[31:25], insn[11:7]};
        imm_b  = {{19{insn[31]}}, insn[31], insn[7], insn[30:25], insn[11:8], 1'b0};
        imm_u  = {insn[31:12], 12'b0};
        imm_j  = {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0};
        funct3 = insn[14:12];
        funct7 = insn[31:25];

        d = '0;
        d.rs1     = insn[19:15];
        d.rs2     = insn[24:20];
        d.rd      = insn[11:7];
        d.funct3  = funct3;
        d.res.add = 1'b1;             // no result written: any one source
        d.illegal = 1'b1;
        if (insn[1:0] == 2'b11) begin
            unique case (insn[6:2])
                5'b01101: begin                                   // lui
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_u; d.res = '0; d.res.imm = 1'b1;
                end
                5'b00101: begin                                   // auipc
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_u; d.res = '0; d.res.pc_imm = 1'b1;
                end
                5'b11011: begin                                   // jal
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_j; d.jal = 1'b1; d.res = '0; d.res.link = 1'b1;
                end
                5'b11001: if (funct3 == 3'd0) begin               // jalr
                    d.illegal = 1'b0; d.uses_rs1 = 1'b1; d.writes_rd = 1'b1; d.imm = imm_i; d.jalr = 1'b1;
                    d.b_imm = 1'b1; d.res = '0; d.res.link = 1'b1;
                end
                5'b11000: if (funct3 != 3'd2 && funct3 != 3'd3) begin   // branches
                    d.illegal = 1'b0; d.uses_rs1 = 1'b1; d.uses_rs2 = 1'b1; d.imm = imm_b; d.branch = 1'b1;
                end
                5'b00000: if (funct3 != 3'd3 && funct3 < 3'd6) begin    // lb lh lw lbu lhu
                    d.illegal = 1'b0; d.uses_rs1 = 1'b1; d.writes_rd = 1'b1; d.imm = imm_i; d.load = 1'b1;
                    d.b_imm = 1'b1;
                end
                5'b01000: if (funct3 < 3'd3) begin                // sb sh sw
                    d.illegal = 1'b0; d.uses_rs1 = 1'b1; d.uses_rs2 = 1'b1; d.imm = imm_s; d.store = 1'b1;
                    d.b_imm = 1'b1;
                end
                5'b00100: begin                                   // OP-IMM
                    d.uses_rs1 = 1'b1; d.writes_rd = 1'b1; d.imm = imm_i; d.b_imm = 1'b1;
                    d.illegal = 1'b0;
                    d.res = '0;
                    unique case (funct3)
                        3'd0: d.res.add = 1'b1;
                        3'd1: begin d.res.sll = 1'b1; d.illegal = funct7 != 7'h00; end
                        3'd2: d.res.slt = 1'b1;
                        3'd3: d.res.sltu = 1'b1;
                        3'd4: d.res.bxor = 1'b1;
                        3'd5: begin
                            if (funct7 == 7'h20) d.res.sra = 1'b1; else d.res.srl = 1'b1;
                            d.illegal = funct7 != 7'h00 && funct7 != 7'h20;
                        end
                        3'd6: d.res.bor = 1'b1;
                        3'd7: d.res.band = 1'b1;
                    endcase
                end
                5'b01100: begin                                   // OP, and the M extension (funct7 1)
                    d.uses_rs1 = 1'b1; d.uses_rs2 = 1'b1; d.writes_rd = 1'b1;
                    if (funct7 == 7'h01) begin
                        d.illegal = 1'b0;
                        d.mul     = !funct3[2];
                        d.div     = funct3[2];
                        if (funct3[2]) begin d.res = '0; d.res.div = 1'b1; end    // a multiply's result comes from W
                    end else if (funct7 == 7'h00) begin
                        d.illegal = 1'b0;
                        d.res = '0;
                        unique case (funct3)
                            3'd0: d.res.add = 1'b1;
                            3'd1: d.res.sll = 1'b1;
                            3'd2: d.res.slt = 1'b1;
                            3'd3: d.res.sltu = 1'b1;
                            3'd4: d.res.bxor = 1'b1;
                            3'd5: d.res.srl = 1'b1;
                            3'd6: d.res.bor = 1'b1;
                            3'd7: d.res.band = 1'b1;
                        endcase
                    end else if (funct7 == 7'h20 && (funct3 == 3'd0 || funct3 == 3'd5)) begin
                        d.illegal = 1'b0;
                        d.res = '0;
                        if (funct3 == 3'd0) d.res.sub = 1'b1; else d.res.sra = 1'b1;
                    end
                end
                5'b00011: begin                                   // fence (a no-op), fence.i
                    d.illegal = funct3 > 3'd1;
                    d.fencei  = funct3 == 3'd1;
                end
                5'b01011: if (funct3 == 3'd2) begin                // RV32A
                    d.uses_rs1 = 1'b1; d.uses_rs2 = 1'b1; d.writes_rd = 1'b1; d.b_imm = 1'b1;
                    d.atomic = 1'b1; d.load = 1'b1; d.store = 1'b1; d.illegal = 1'b0;
                    unique case (insn[31:27])
                        5'b00010: begin                           // lr.w (rs2 must be 0)
                            d.amo_op = OP_LR; d.store = 1'b0; d.uses_rs2 = 1'b0;
                            d.illegal = insn[24:20] != 5'd0;
                        end
                        5'b00011: d.amo_op = OP_SC;
                        5'b00001: d.amo_op = OP_SWAP;
                        5'b00000: d.amo_op = OP_ADD;
                        5'b00100: d.amo_op = OP_XOR;
                        5'b01100: d.amo_op = OP_AND;
                        5'b01000: d.amo_op = OP_OR;
                        5'b10000: d.amo_op = OP_MIN;
                        5'b10100: d.amo_op = OP_MAX;
                        5'b11000: d.amo_op = OP_MINU;
                        5'b11100: d.amo_op = OP_MAXU;
                        default:  d.illegal = 1'b1;
                    endcase
                end
                5'b11100: begin                                   // SYSTEM
                    if (funct3 == 3'd0) begin
                        // ecall, ebreak, mret, and wfi (a no-op: an interrupt is
                        // taken between instructions whatever they are)
                        d.ecall   = insn == 32'h0000_0073;
                        d.ebreak  = insn == 32'h0010_0073;
                        d.mret    = insn == 32'h3020_0073;
                        d.illegal = !(d.ecall || d.ebreak || d.mret || insn == 32'h1050_0073);
                        d.sys     = d.mret;
                    end else if (funct3 != 3'd4) begin
                        // csrrw csrrs csrrc (rs1), csrrwi csrrsi csrrci (uimm in the rs1 field)
                        d.csr       = 1'b1;
                        d.sys       = 1'b1;
                        d.csr_sel   = csr_decode(insn[31:20]);
                        d.csr_write = funct3[1:0] == 2'd1 || insn[19:15] != 5'd0;
                        d.uses_rs1  = !funct3[2];
                        d.writes_rd = 1'b1;
                        d.imm       = {27'b0, insn[19:15]};
                        d.res = '0; d.res.csr = 1'b1;
                        // An unimplemented CSR, or a write to a read-only one, is illegal.
                        d.illegal   = d.csr_sel == '0 || (d.csr_write && insn[31:30] == 2'b11);
                    end
                end
                default: ;
            endcase
        end
        if (d.illegal) begin
            d.uses_rs1 = 1'b0; d.uses_rs2 = 1'b0; d.writes_rd = 1'b0;
            d.load = 1'b0; d.store = 1'b0; d.branch = 1'b0; d.jal = 1'b0; d.jalr = 1'b0;
            d.mul = 1'b0; d.div = 1'b0;
            d.csr = 1'b0; d.csr_write = 1'b0; d.csr_sel = '0; d.ecall = 1'b0; d.ebreak = 1'b0; d.mret = 1'b0;
            d.sys = 1'b0; d.atomic = 1'b0; d.amo_op = '0; d.fencei = 1'b0;
            d.res = '0; d.res.add = 1'b1;
        end
        if (d.rd == 5'd0) d.writes_rd = 1'b0;
        return d;
    endfunction

    // Execute's result: the selected source (sel is one-hot). Operand A is rs1;
    // operand B is rs2 or the immediate.
    function automatic logic [31:0] result(input result_t sel, input logic [31:0] a, input logic [31:0] b,
                                           input logic [31:0] imm, input logic [31:0] pc_imm,
                                           input logic [31:0] link, input logic [31:0] quotient,
                                           input logic [31:0] csr);
        return ({32{sel.add}}    & (a + b))
             | ({32{sel.sub}}    & (a - b))
             | ({32{sel.sll}}    & (a << b[4:0]))
             | ({32{sel.slt}}    & {31'b0, $signed(a) < $signed(b)})
             | ({32{sel.sltu}}   & {31'b0, a < b})
             | ({32{sel.bxor}}   & (a ^ b))
             | ({32{sel.srl}}    & (a >> b[4:0]))
             | ({32{sel.sra}}    & $unsigned($signed(a) >>> b[4:0]))
             | ({32{sel.bor}}    & (a | b))
             | ({32{sel.band}}   & (a & b))
             | ({32{sel.imm}}    & imm)
             | ({32{sel.pc_imm}} & pc_imm)
             | ({32{sel.link}}   & link)
             | ({32{sel.div}}    & quotient)
             | ({32{sel.csr}}    & csr);
    endfunction

    // Predecode, as an instruction enters Decode: {predicted taken from Decode,
    // target}. A `jal`, or a conditional branch with a negative offset
    // (backward, predicted taken), redirects from Decode when its target is
    // aligned; the same fields decode() reads, so the two agree.
    function automatic logic [32:0] predecode(input logic [31:0] insn, input logic [31:0] pc);
        logic        jal, branch;
        logic [31:0] imm, target;
        jal    = insn[6:0] == 7'b1101111;
        branch = insn[6:0] == 7'b1100011 && insn[14:12] != 3'd2 && insn[14:12] != 3'd3;
        imm    = jal ? {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0}
                     : {{19{insn[31]}}, insn[31], insn[7], insn[30:25], insn[11:8], 1'b0};
        target = pc + imm;
        return {(jal || (branch && insn[31])) && !target[1], target};
    endfunction

    // An AMO's new value from the old one and rs2 (funct5 selects); for RVFI,
    // and the CPU shell's memory computes the same.
    function automatic logic [31:0] amo_value(input logic [4:0] funct5, input logic [31:0] old,
                                              input logic [31:0] operand);
        unique case (funct5)
            5'b00000: return old + operand;
            5'b00100: return old ^ operand;
            5'b01100: return old & operand;
            5'b01000: return old | operand;
            5'b10000: return $signed(old) < $signed(operand) ? old : operand;
            5'b10100: return $signed(old) > $signed(operand) ? old : operand;
            5'b11000: return old < operand ? old : operand;
            5'b11100: return old > operand ? old : operand;
            default:  return operand;                           // amoswap (and sc's data)
        endcase
    endfunction

    // A load's register value from the aligned 32-bit word it read.
    function automatic logic [31:0] load_value(input logic [31:0] word, input logic [1:0] offset,
                                               input logic [2:0] funct3);
        logic [31:0] shifted;
        shifted = word >> {offset, 3'b000};
        unique case (funct3)
            3'd0: return {{24{shifted[7]}}, shifted[7:0]};      // lb
            3'd1: return {{16{shifted[15]}}, shifted[15:0]};    // lh
            3'd4: return {24'b0, shifted[7:0]};                 // lbu
            3'd5: return {16'b0, shifted[15:0]};                // lhu
            default: return shifted;                            // lw (aligned: offset 0)
        endcase
    endfunction

    // One radix-2 restoring division step: {remainder, quotient} shifts left by
    // one, the dividend's next bit entering the remainder, and the divisor is
    // subtracted from the remainder when it fits, setting the quotient's new bit.
    function automatic logic [63:0] divide_step(input logic [31:0] remainder, input logic [31:0] quotient,
                                                input logic [31:0] divisor);
        logic [32:0] shifted, difference;
        shifted    = {remainder, quotient[31]};
        difference = shifted - {1'b0, divisor};
        return difference[32] ? {shifted[31:0], quotient[30:0], 1'b0}
                              : {difference[31:0], quotient[30:0], 1'b1};
    endfunction

    function automatic logic branch_taken(input logic [2:0] funct3, input logic [31:0] a, input logic [31:0] b);
        unique case (funct3)
            3'd0: return a == b;
            3'd1: return a != b;
            3'd4: return $signed(a) < $signed(b);
            3'd5: return $signed(a) >= $signed(b);
            3'd6: return a < b;
            default: return a >= b;           // 3'd7 (2 and 3 decode as illegal)
        endcase
    endfunction
endpackage
