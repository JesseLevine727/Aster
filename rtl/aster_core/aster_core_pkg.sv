// Aster core decoder, ALU and branch compare (docs/cpu.md §2, §4). Milestone
// 18.1 implements RV32I; every other encoding — the M, A, Zicsr and Zifencei
// instructions, `ecall`, `ebreak` and the rest of SYSTEM, custom-0 — decodes
// as illegal until its milestone adds it. `fence` executes as a no-op: the
// core's accesses take effect in order at one memory (docs/cpu.md §5).
`timescale 1 ns / 1 ps
package aster_core_pkg;
    typedef enum logic [3:0] {
        ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU, ALU_XOR, ALU_SRL, ALU_SRA, ALU_OR, ALU_AND, ALU_B
    } alu_op_e;

    typedef struct packed {
        logic        illegal;
        logic        uses_rs1;
        logic        uses_rs2;
        logic        writes_rd;       // and rd is not x0
        logic [4:0]  rs1;
        logic [4:0]  rs2;
        logic [4:0]  rd;
        logic [31:0] imm;
        alu_op_e     alu_op;
        logic        a_pc;            // ALU operand A is the PC (auipc)
        logic        b_imm;           // ALU operand B is the immediate
        logic        load;
        logic        store;
        logic        branch;
        logic        jal;
        logic        jalr;
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
        d.alu_op  = ALU_ADD;
        d.illegal = 1'b1;
        if (insn[1:0] == 2'b11) begin
            unique case (insn[6:2])
                5'b01101: begin                                   // lui
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_u; d.alu_op = ALU_B; d.b_imm = 1'b1;
                end
                5'b00101: begin                                   // auipc
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_u; d.a_pc = 1'b1; d.b_imm = 1'b1;
                end
                5'b11011: begin                                   // jal
                    d.illegal = 1'b0; d.writes_rd = 1'b1; d.imm = imm_j; d.jal = 1'b1;
                end
                5'b11001: if (funct3 == 3'd0) begin               // jalr
                    d.illegal = 1'b0; d.uses_rs1 = 1'b1; d.writes_rd = 1'b1; d.imm = imm_i; d.jalr = 1'b1;
                    d.b_imm = 1'b1;
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
                    unique case (funct3)
                        3'd0: d.alu_op = ALU_ADD;
                        3'd1: begin d.alu_op = ALU_SLL; d.illegal = funct7 != 7'h00; end
                        3'd2: d.alu_op = ALU_SLT;
                        3'd3: d.alu_op = ALU_SLTU;
                        3'd4: d.alu_op = ALU_XOR;
                        3'd5: begin
                            d.alu_op = funct7 == 7'h20 ? ALU_SRA : ALU_SRL;
                            d.illegal = funct7 != 7'h00 && funct7 != 7'h20;
                        end
                        3'd6: d.alu_op = ALU_OR;
                        3'd7: d.alu_op = ALU_AND;
                    endcase
                end
                5'b01100: begin                                   // OP (the M encodings, funct7 1, come in 18.2)
                    d.uses_rs1 = 1'b1; d.uses_rs2 = 1'b1; d.writes_rd = 1'b1;
                    if (funct7 == 7'h00) begin
                        d.illegal = 1'b0;
                        unique case (funct3)
                            3'd0: d.alu_op = ALU_ADD;
                            3'd1: d.alu_op = ALU_SLL;
                            3'd2: d.alu_op = ALU_SLT;
                            3'd3: d.alu_op = ALU_SLTU;
                            3'd4: d.alu_op = ALU_XOR;
                            3'd5: d.alu_op = ALU_SRL;
                            3'd6: d.alu_op = ALU_OR;
                            3'd7: d.alu_op = ALU_AND;
                        endcase
                    end else if (funct7 == 7'h20 && (funct3 == 3'd0 || funct3 == 3'd5)) begin
                        d.illegal = 1'b0;
                        d.alu_op = funct3 == 3'd0 ? ALU_SUB : ALU_SRA;
                    end
                end
                5'b00011: if (funct3 == 3'd0) d.illegal = 1'b0;   // fence (fence.i comes in 18.4)
                default: ;
            endcase
        end
        if (d.illegal) begin
            d.uses_rs1 = 1'b0; d.uses_rs2 = 1'b0; d.writes_rd = 1'b0;
            d.load = 1'b0; d.store = 1'b0; d.branch = 1'b0; d.jal = 1'b0; d.jalr = 1'b0;
        end
        if (d.rd == 5'd0) d.writes_rd = 1'b0;
        return d;
    endfunction

    function automatic logic [31:0] alu(input alu_op_e op, input logic [31:0] a, input logic [31:0] b);
        unique case (op)
            ALU_ADD:  return a + b;
            ALU_SUB:  return a - b;
            ALU_SLL:  return a << b[4:0];
            ALU_SLT:  return {31'b0, $signed(a) < $signed(b)};
            ALU_SLTU: return {31'b0, a < b};
            ALU_XOR:  return a ^ b;
            ALU_SRL:  return a >> b[4:0];
            ALU_SRA:  return $unsigned($signed(a) >>> b[4:0]);
            ALU_OR:   return a | b;
            ALU_AND:  return a & b;
            default:  return b;               // ALU_B
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
