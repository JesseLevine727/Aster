// Minimal single-issue RV32I core for the Phase 0/1 bring-up path.
//
// This is intentionally small and blocking: instruction and data reads are
// combinational, and stores commit on the rising clock edge. It is a bring-up
// core, not the intended final microarchitecture for Aster v1.
module rv32i_core (
    input  logic        clk,
    input  logic        rst_n,

    output logic [31:0] instr_addr,
    input  logic [31:0] instr_rdata,

    output logic [31:0] data_addr,
    output logic [31:0] data_wdata,
    output logic [3:0]  data_wstrb,
    output logic        data_we,
    input  logic [31:0] data_rdata,

    output logic [31:0] pc_debug,
    output logic        illegal_instruction
);
    logic [31:0] registers [0:31];
    logic [31:0] pc_q;
    logic [31:0] next_pc;
    logic        writeback_en;
    logic [4:0]  writeback_rd;
    logic [31:0] writeback_data;
    logic [31:0] rs1_value;
    logic [31:0] rs2_value;
    logic [31:0] load_byte;
    logic [31:0] load_half;
    integer index;

    logic [6:0] opcode;
    logic [2:0] funct3;
    logic [6:0] funct7;
    logic [4:0] rs1;
    logic [4:0] rs2;
    logic [4:0] rd;
    logic [4:0] shamt;
    logic signed [31:0] imm_i;
    logic signed [31:0] imm_s;
    logic signed [31:0] imm_b;
    logic signed [31:0] imm_u;
    logic signed [31:0] imm_j;

    assign instr_addr = pc_q;
    assign pc_debug = pc_q;

    always_comb begin
        opcode = instr_rdata[6:0];
        funct3 = instr_rdata[14:12];
        funct7 = instr_rdata[31:25];
        rs1 = instr_rdata[19:15];
        rs2 = instr_rdata[24:20];
        rd = instr_rdata[11:7];
        shamt = instr_rdata[24:20];

        imm_i = {{20{instr_rdata[31]}}, instr_rdata[31:20]};
        imm_s = {{20{instr_rdata[31]}}, instr_rdata[31:25], instr_rdata[11:7]};
        imm_b = {{19{instr_rdata[31]}}, instr_rdata[31], instr_rdata[7],
                instr_rdata[30:25], instr_rdata[11:8], 1'b0};
        imm_u = {instr_rdata[31:12], 12'b0};
        imm_j = {{11{instr_rdata[31]}}, instr_rdata[31], instr_rdata[19:12],
                instr_rdata[20], instr_rdata[30:21], 1'b0};

        rs1_value = (rs1 == 5'd0) ? 32'd0 : registers[rs1];
        rs2_value = (rs2 == 5'd0) ? 32'd0 : registers[rs2];
    end

    always_comb begin
        case (data_addr[1:0])
            2'd0: load_byte = {24'd0, data_rdata[7:0]};
            2'd1: load_byte = {24'd0, data_rdata[15:8]};
            2'd2: load_byte = {24'd0, data_rdata[23:16]};
            default: load_byte = {24'd0, data_rdata[31:24]};
        endcase

        load_half = data_addr[1]
            ? {16'd0, data_rdata[31:16]}
            : {16'd0, data_rdata[15:0]};
    end

    always_comb begin
        next_pc = pc_q + 32'd4;
        writeback_en = 1'b0;
        writeback_rd = rd;
        writeback_data = 32'd0;
        data_addr = 32'd0;
        data_wdata = 32'd0;
        data_wstrb = 4'b0000;
        data_we = 1'b0;
        illegal_instruction = 1'b0;

        case (opcode)
            7'b0110111: begin // LUI
                writeback_en = (rd != 5'd0);
                writeback_data = imm_u;
            end

            7'b0010111: begin // AUIPC
                writeback_en = (rd != 5'd0);
                writeback_data = pc_q + imm_u;
            end

            7'b1101111: begin // JAL
                writeback_en = (rd != 5'd0);
                writeback_data = pc_q + 32'd4;
                next_pc = pc_q + imm_j;
            end

            7'b1100111: begin // JALR
                if (funct3 == 3'b000) begin
                    writeback_en = (rd != 5'd0);
                    writeback_data = pc_q + 32'd4;
                    next_pc = (rs1_value + imm_i) & 32'hffff_fffe;
                end else begin
                    illegal_instruction = 1'b1;
                end
            end

            7'b1100011: begin // Conditional branches
                case (funct3)
                    3'b000: if (rs1_value == rs2_value) next_pc = pc_q + imm_b; // BEQ
                    3'b001: if (rs1_value != rs2_value) next_pc = pc_q + imm_b; // BNE
                    3'b100: if ($signed(rs1_value) < $signed(rs2_value)) next_pc = pc_q + imm_b; // BLT
                    3'b101: if ($signed(rs1_value) >= $signed(rs2_value)) next_pc = pc_q + imm_b; // BGE
                    3'b110: if (rs1_value < rs2_value) next_pc = pc_q + imm_b; // BLTU
                    3'b111: if (rs1_value >= rs2_value) next_pc = pc_q + imm_b; // BGEU
                    default: illegal_instruction = 1'b1;
                endcase
            end

            7'b0000011: begin // Loads
                data_addr = rs1_value + imm_i;
                case (funct3)
                    3'b000: begin // LB
                        writeback_en = (rd != 5'd0);
                        writeback_data = {{24{load_byte[7]}}, load_byte[7:0]};
                    end
                    3'b001: begin // LH
                        writeback_en = (rd != 5'd0);
                        writeback_data = {{16{load_half[15]}}, load_half[15:0]};
                    end
                    3'b010: begin // LW
                        writeback_en = (rd != 5'd0);
                        writeback_data = data_rdata;
                    end
                    3'b100: begin // LBU
                        writeback_en = (rd != 5'd0);
                        writeback_data = load_byte;
                    end
                    3'b101: begin // LHU
                        writeback_en = (rd != 5'd0);
                        writeback_data = load_half;
                    end
                    default: illegal_instruction = 1'b1;
                endcase
            end

            7'b0100011: begin // Stores
                data_addr = rs1_value + imm_s;
                case (funct3)
                    3'b000: begin // SB
                        data_we = 1'b1;
                        data_wstrb = 4'b0001 << data_addr[1:0];
                        data_wdata = rs2_value << (data_addr[1:0] * 8);
                    end
                    3'b001: begin // SH
                        data_we = 1'b1;
                        data_wstrb = data_addr[1]
                            ? 4'b1100
                            : 4'b0011;
                        data_wdata = rs2_value << (data_addr[1:0] * 8);
                    end
                    3'b010: begin // SW
                        data_we = 1'b1;
                        data_wstrb = 4'b1111;
                        data_wdata = rs2_value;
                    end
                    default: illegal_instruction = 1'b1;
                endcase
            end

            7'b0010011: begin // Immediate ALU operations
                case (funct3)
                    3'b000: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value + imm_i; end // ADDI
                    3'b010: begin writeback_en = (rd != 5'd0); writeback_data = ($signed(rs1_value) < $signed(imm_i)) ? 32'd1 : 32'd0; end // SLTI
                    3'b011: begin writeback_en = (rd != 5'd0); writeback_data = (rs1_value < $unsigned(imm_i)) ? 32'd1 : 32'd0; end // SLTIU
                    3'b100: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value ^ imm_i; end // XORI
                    3'b110: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value | imm_i; end // ORI
                    3'b111: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value & imm_i; end // ANDI
                    3'b001: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value << shamt; end // SLLI
                    3'b101: begin
                        writeback_en = (rd != 5'd0);
                        writeback_data = (funct7 == 7'b0100000)
                            ? ($signed(rs1_value) >>> shamt)
                            : (rs1_value >> shamt); // SRAI/SRLI
                    end
                    default: illegal_instruction = 1'b1;
                endcase
            end

            7'b0110011: begin // Register-register ALU operations
                case (funct3)
                    3'b000: begin
                        writeback_en = (rd != 5'd0);
                        writeback_data = (funct7 == 7'b0100000)
                            ? rs1_value - rs2_value
                            : rs1_value + rs2_value; // SUB/ADD
                    end
                    3'b001: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value << rs2_value[4:0]; end // SLL
                    3'b010: begin writeback_en = (rd != 5'd0); writeback_data = ($signed(rs1_value) < $signed(rs2_value)) ? 32'd1 : 32'd0; end // SLT
                    3'b011: begin writeback_en = (rd != 5'd0); writeback_data = (rs1_value < rs2_value) ? 32'd1 : 32'd0; end // SLTU
                    3'b100: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value ^ rs2_value; end // XOR
                    3'b101: begin
                        writeback_en = (rd != 5'd0);
                        writeback_data = (funct7 == 7'b0100000)
                            ? ($signed(rs1_value) >>> rs2_value[4:0])
                            : (rs1_value >> rs2_value[4:0]); // SRA/SRL
                    end
                    3'b110: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value | rs2_value; end // OR
                    3'b111: begin writeback_en = (rd != 5'd0); writeback_data = rs1_value & rs2_value; end // AND
                    default: illegal_instruction = 1'b1;
                endcase
            end

            7'b0001111: begin // FENCE: a no-op in the uncached bring-up core
            end

            7'b1110011: begin // SYSTEM: ECALL/EBREAK are reserved for trap work
                if (instr_rdata != 32'h0000_0073 && instr_rdata != 32'h0010_0073)
                    illegal_instruction = 1'b1;
            end

            default: illegal_instruction = 1'b1;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pc_q <= 32'd0;
            for (index = 0; index < 32; index = index + 1)
                registers[index] <= 32'd0;
        end else begin
            pc_q <= next_pc;
            if (writeback_en && (writeback_rd != 5'd0))
                registers[writeback_rd] <= writeback_data;
            registers[0] <= 32'd0;
        end
    end
endmodule
