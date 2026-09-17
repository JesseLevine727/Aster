// Phase 9.4: CPU-facing NPU descriptor/control register boundary.
// Register accesses are one-cycle request/response transactions; the engine
// owns memory traffic and all job accounting.
`timescale 1 ns / 1 ps
module aster_npu_regs #(
    parameter int ROWS = 4,
    parameter int COLS = 4
) (
    input  logic               clk,
    input  logic               resetn,
    input  logic               global_stop,
    input  logic               req_valid,
    input  logic               req_write,
    input  logic [11:0]        req_addr,
    input  logic [31:0]        req_wdata,
    input  logic [3:0]         req_wstrb,
    output logic               req_ready,
    output logic [31:0]        req_rdata,
    output logic               busy,
    output logic               done,
    output logic               error,
    output logic               aborted,
    output logic [4:0]         status,
    output logic [31:0]        error_code,
    output logic [31:0]        bytes_read,
    output logic [31:0]        bytes_written,
    output logic [63:0]        job_cycles,
    output logic [63:0]        compute_cycles,
    output logic [31:0]        tiles,
    output logic               m_valid,
    output logic               m_write,
    output logic [31:0]        m_addr,
    output logic [31:0]        m_wdata,
    output logic [3:0]         m_wstrb,
    input  logic               m_ready,
    input  logic [31:0]        m_rdata
);
    logic [31:0] a_base, b_base, c_base;
    logic [31:0] a_stride, b_stride, c_stride;
    logic [31:0] rows, cols, reduction;
    logic reject_flag;
    logic [31:0] reject_code;
    logic engine_start, engine_abort, engine_ack;
    logic engine_busy, engine_done, engine_error, engine_aborted;
    logic [4:0] engine_status;
    logic [31:0] engine_error_code;
    logic [31:0] engine_bytes_read, engine_bytes_written, engine_tiles;
    logic [63:0] engine_job_cycles, engine_compute_cycles;

    logic request_fire, descriptor_write, control_write, control_valid;
    logic command_start, command_abort, command_ack, command_known;

    assign req_ready = resetn;
    assign request_fire = resetn && req_valid && req_ready;
    assign descriptor_write = request_fire && req_write && !global_stop && !engine_busy &&
                              (req_addr == 12'h010 || req_addr == 12'h014 || req_addr == 12'h018 ||
                               req_addr == 12'h01c || req_addr == 12'h020 || req_addr == 12'h024 ||
                               req_addr == 12'h028 || req_addr == 12'h02c || req_addr == 12'h030);
    assign control_write = request_fire && req_write && req_addr == 12'h000;
    // PicoRV32 replicates an SB value across all data lanes while its byte
    // strobe identifies the selected lane. CONTROL is lane-0 only, so reject
    // upper strobes but decode the lane-0 byte rather than its replicated copy.
    assign control_valid = control_write && req_wstrb == 4'b0001;
    assign command_start = control_valid && req_wdata[7:0] == 8'd1;
    assign command_abort = control_valid && req_wdata[7:0] == 8'd2;
    assign command_ack = control_valid && req_wdata[7:0] == 8'd4;
    assign command_known = control_valid &&
                           (req_wdata[7:0] == 8'd0 || command_start || command_abort || command_ack);
    assign engine_start = command_start && !engine_busy && !global_stop;
    assign engine_abort = command_abort && engine_busy;
    assign engine_ack = command_ack && !engine_busy && !global_stop;

    aster_npu_engine #(.ROWS(ROWS), .COLS(COLS)) engine (
        .clk(clk), .resetn(resetn), .start(engine_start),
        .start_a_base(a_base), .start_b_base(b_base), .start_c_base(c_base),
        .start_a_stride(a_stride), .start_b_stride(b_stride), .start_c_stride(c_stride),
        .start_m(rows), .start_n(cols), .start_k(reduction),
        .pause(global_stop), .abort_request(global_stop || engine_abort), .ack(engine_ack),
        .busy(engine_busy), .done(engine_done),
        .error(engine_error), .aborted(engine_aborted), .error_code(engine_error_code),
        .bytes_read(engine_bytes_read), .bytes_written(engine_bytes_written),
        .job_cycles(engine_job_cycles), .compute_cycles(engine_compute_cycles),
        .tiles(engine_tiles), .status(engine_status),
        .m_valid(m_valid), .m_write(m_write), .m_addr(m_addr), .m_wdata(m_wdata),
        .m_wstrb(m_wstrb), .m_ready(m_ready), .m_rdata(m_rdata)
    );

    assign busy = engine_busy;
    assign done = engine_done;
    assign error = engine_status[2] || reject_flag;
    assign aborted = engine_aborted;
    assign status = {engine_status[4], engine_status[3], error, engine_status[1], engine_status[0]};
    assign error_code = engine_error ? engine_error_code : reject_code;
    assign bytes_read = engine_bytes_read;
    assign bytes_written = engine_bytes_written;
    assign job_cycles = engine_job_cycles;
    assign compute_cycles = engine_compute_cycles;
    assign tiles = engine_tiles;

    always_comb begin
        req_rdata = 0;
        if (resetn) case (req_addr)
            12'h004: req_rdata = {27'd0, status};
            12'h008: req_rdata = 32'd1;
            12'h00c: req_rdata = 32'd1;
            12'h010: req_rdata = a_base;
            12'h014: req_rdata = b_base;
            12'h018: req_rdata = c_base;
            12'h01c: req_rdata = a_stride;
            12'h020: req_rdata = b_stride;
            12'h024: req_rdata = c_stride;
            12'h028: req_rdata = rows;
            12'h02c: req_rdata = cols;
            12'h030: req_rdata = reduction;
            12'h034: req_rdata = error_code;
            12'h038: req_rdata = bytes_read;
            12'h03c: req_rdata = bytes_written;
            12'h040: req_rdata = job_cycles[31:0];
            12'h044: req_rdata = job_cycles[63:32];
            12'h048: req_rdata = compute_cycles[31:0];
            12'h04c: req_rdata = compute_cycles[63:32];
            12'h050: req_rdata = tiles;
            // bit 0: 4x4 geometry (ROWS==COLS==4); bit 1: signed INT8; bit 2: byte
            // strides; bit 3: exact byte-lane stores; bit 4: coherent-device contract.
            12'h054: req_rdata = (ROWS == 4 && COLS == 4) ? 32'h0000_001f : 32'h0000_001e;
            12'h058: req_rdata = {16'd0, 8'(ROWS), 8'(COLS)};
            default: req_rdata = 0;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            a_base <= 0; b_base <= 0; c_base <= 0;
            a_stride <= 0; b_stride <= 0; c_stride <= 0;
            rows <= 0; cols <= 0; reduction <= 0;
            reject_flag <= 0; reject_code <= 0;
        end else begin
            if (engine_start || engine_ack) begin
                reject_flag <= 0;
                reject_code <= 0;
            end
            if (engine_ack) begin
                reject_flag <= 0;
                reject_code <= 0;
            end
            if (request_fire && req_write && req_addr == 12'h000) begin
                if (!control_valid || !command_known) begin
                    reject_flag <= 1;
                    reject_code <= 32'd7;
                end
            end
            if (request_fire && req_write && req_addr >= 12'h010 && req_addr <= 12'h030 &&
                (req_addr[1:0] != 0 || !descriptor_write)) begin
                if (!engine_busy) begin
                    reject_flag <= 1;
                    reject_code <= 32'd7;
                end
            end
            if (descriptor_write) begin
                for (int byte_lane = 0; byte_lane < 4; byte_lane++) begin
                    if (req_wstrb[byte_lane]) case (req_addr)
                        12'h010: a_base[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h014: b_base[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h018: c_base[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h01c: a_stride[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h020: b_stride[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h024: c_stride[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h028: rows[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h02c: cols[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        12'h030: reduction[8*byte_lane +: 8] <= req_wdata[8*byte_lane +: 8];
                        default: ;
                    endcase
                end
            end
        end
    end
endmodule
