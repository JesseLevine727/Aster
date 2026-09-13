// Autonomous, single-descriptor shared-RAM memcpy engine. The memory port is
// an uncached *coherent-service* requester, never a bypass to dirty backing RAM.
// cfg_valid is one accepted register transaction (not a held upstream offer).
// Pause prevents new offers; abort drains an already offered request unchanged.
`timescale 1 ns / 1 ps
module aster_dma_engine (
    input logic clk,
    input logic resetn,
    input logic cfg_valid,
    input logic cfg_owner,
    input logic [11:0] cfg_addr,
    input logic [31:0] cfg_wdata,
    input logic [3:0] cfg_wstrb,
    output logic [31:0] cfg_rdata,
    input logic pause,
    input logic abort_request,
    output logic m_valid,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata,
    output logic busy,
    output logic completion,
    output logic [4:0] status,
    output logic [31:0] bytes_done,
    output logic [31:0] error_code,
    output logic [63:0] job_cycles,
    // Combinational events consumed on the same rising edge as the operation.
    // Final payload-byte accounting belongs at the actual RAM acceptance edge;
    // event_write is the (possibly later) coherent front-port response.
    output logic event_read,
    output logic event_write,
    output logic event_success,
    output logic event_abort,
    output logic event_error,
    output logic event_reject
);
    localparam logic [31:0] LIMIT_LO = 32'h1000_0000;
    localparam logic [31:0] LIMIT_HI = 32'h1000_8000;
    typedef enum logic [2:0] {IDLE, ISSUE_READ, WAIT_READ, ISSUE_WRITE, WAIT_WRITE} state_t;
    state_t state;
    logic [31:0] source, destination, length;
    logic [31:0] next_source, next_destination, remaining, read_buffer;
    logic [2:0] chunk;
    logic done, error_flag, aborted, rejected, abort_pending;
    logic config_write, command_write, start_command, abort_command, ack_command;
    logic start_accept, abort_now, terminal_abort, terminal_success;
    logic [32:0] source_end, destination_end;
    logic [31:0] descriptor_error;

    assign busy = resetn && state != IDLE;
    assign m_valid = resetn && (state == WAIT_READ || state == WAIT_WRITE);
    assign completion = resetn && done;
    assign status = {rejected, aborted, error_flag, done, busy};
    assign config_write = cfg_valid && !cfg_owner && |cfg_wstrb &&
                          (cfg_addr == 12'h000 || cfg_addr == 12'h004 || cfg_addr == 12'h008);
    assign command_write = cfg_valid && !cfg_owner && cfg_wstrb[0] && cfg_addr == 12'h00c;
    assign start_command = command_write && cfg_wdata[7:0] == 8'd1;
    assign abort_command = command_write && cfg_wdata[7:0] == 8'd2;
    assign ack_command = command_write && cfg_wdata[7:0] == 8'd4;
    assign start_accept = resetn && start_command && !busy && !pause && !abort_request;
    assign abort_now = abort_pending || abort_request || abort_command;
    assign terminal_abort = busy && abort_now &&
        (state == ISSUE_READ || state == ISSUE_WRITE || (m_valid && m_ready));
    assign terminal_success = busy && !abort_now && state == WAIT_WRITE && m_ready &&
                              remaining == {29'd0, chunk};
    assign event_read = m_valid && m_ready && state == WAIT_READ;
    assign event_write = m_valid && m_ready && state == WAIT_WRITE;
    assign event_abort = terminal_abort;
    assign event_success = terminal_success || (start_accept && length == 0);
    assign event_error = start_accept && length != 0 && descriptor_error != 0;
    assign event_reject = resetn && ((config_write && busy) ||
        (command_write && !start_command && !abort_command && !ack_command) ||
        (start_command && !start_accept) || (ack_command && busy));

    // 33-bit ends detect overflow before the first payload memory access.
    always_comb begin
        source_end = {1'b0, source} + {1'b0, length};
        destination_end = {1'b0, destination} + {1'b0, length};
        descriptor_error = 0;
        if (source < LIMIT_LO || source_end > {1'b0, LIMIT_HI}) descriptor_error = 1;
        else if (destination < LIMIT_LO || destination_end > {1'b0, LIMIT_HI}) descriptor_error = 2;
        else if ({1'b0, source} < destination_end && {1'b0, destination} < source_end)
            descriptor_error = 3;
    end

    always_comb begin
        cfg_rdata = 0;
        case (cfg_addr)
            12'h000: cfg_rdata = source;
            12'h004: cfg_rdata = destination;
            12'h008: cfg_rdata = length;
            12'h010: cfg_rdata = {27'd0, status};
            12'h014: cfg_rdata = bytes_done;
            12'h018: cfg_rdata = error_code;
            12'h01c: cfg_rdata = 1;
            12'h020: cfg_rdata = job_cycles[31:0];
            12'h024: cfg_rdata = job_cycles[63:32];
            12'h028: cfg_rdata = LIMIT_LO;
            12'h02c: cfg_rdata = LIMIT_HI;
            default: ;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            source <= 0; destination <= 0; length <= 0;
            next_source <= 0; next_destination <= 0; remaining <= 0;
            read_buffer <= 0; chunk <= 0;
            m_addr <= 0; m_wdata <= 0; m_wstrb <= 0;
            done <= 0; error_flag <= 0; aborted <= 0; rejected <= 0;
            abort_pending <= 0; bytes_done <= 0; error_code <= 0; job_cycles <= 0;
        end else begin
            if (busy) begin
                job_cycles <= job_cycles + 64'd1;
                if (abort_now) abort_pending <= 1;
            end
            if (event_reject) rejected <= 1;
            if (config_write && !busy) begin
                for (int b = 0; b < 4; b++) if (cfg_wstrb[b]) begin
                    case (cfg_addr)
                        12'h000: source[8*b +: 8] <= cfg_wdata[8*b +: 8];
                        12'h004: destination[8*b +: 8] <= cfg_wdata[8*b +: 8];
                        12'h008: length[8*b +: 8] <= cfg_wdata[8*b +: 8];
                        default: ;
                    endcase
                end
            end
            if (ack_command && !busy) begin
                done <= 0; error_flag <= 0; aborted <= 0; rejected <= 0; error_code <= 0;
            end
            case (state)
                IDLE: if (start_accept) begin
                    done <= 0; error_flag <= 0; aborted <= 0; rejected <= 0;
                    abort_pending <= 0; bytes_done <= 0; job_cycles <= 0; error_code <= 0;
                    next_source <= source; next_destination <= destination; remaining <= length;
                    if (length == 0) done <= 1;
                    else if (descriptor_error != 0) begin
                        done <= 1; error_flag <= 1; error_code <= descriptor_error;
                    end else state <= ISSUE_READ;
                end
                ISSUE_READ: if (!abort_now && !pause) begin
                    chunk <= next_source[1:0] == 0 && next_destination[1:0] == 0 && remaining >= 4
                             ? 3'd4 : 3'd1;
                    m_addr <= {next_source[31:2], 2'b00};
                    m_wdata <= 0; m_wstrb <= 0;
                    state <= WAIT_READ;
                end
                WAIT_READ: if (m_ready && !abort_now) begin
                    read_buffer <= chunk == 4 ? m_rdata :
                        ((m_rdata >> (8 * next_source[1:0])) & 32'hff) << (8 * next_destination[1:0]);
                    state <= ISSUE_WRITE;
                end
                ISSUE_WRITE: if (!abort_now && !pause) begin
                    m_addr <= {next_destination[31:2], 2'b00};
                    m_wdata <= read_buffer;
                    m_wstrb <= chunk == 4 ? 4'b1111 : 4'b0001 << next_destination[1:0];
                    state <= WAIT_WRITE;
                end
                WAIT_WRITE: if (m_ready) begin
                    bytes_done <= bytes_done + {29'd0, chunk};
                    remaining <= remaining - {29'd0, chunk};
                    next_source <= next_source + {29'd0, chunk};
                    next_destination <= next_destination + {29'd0, chunk};
                    if (!abort_now && !terminal_success) state <= ISSUE_READ;
                end
                default: state <= IDLE;
            endcase
            if (terminal_success || terminal_abort) begin
                state <= IDLE;
                done <= 1;
                aborted <= terminal_abort;
                abort_pending <= 0;
            end
        end
    end

`ifndef SYNTHESIS
    logic held;
    logic [31:0] held_addr, held_data;
    logic [3:0] held_strobes;
    always_ff @(posedge clk) begin
        if (!resetn) held <= 0;
        else begin
            if (held) assert (m_valid && m_addr == held_addr && m_wdata == held_data && m_wstrb == held_strobes)
                else $fatal(1, "DMA withdrew or changed an offered transaction");
            held <= m_valid && !m_ready;
            held_addr <= m_addr; held_data <= m_wdata; held_strobes <= m_wstrb;
            if (m_valid) begin
                assert (m_addr >= LIMIT_LO && m_addr < LIMIT_HI && m_addr[1:0] == 0)
                    else $fatal(1, "DMA escaped permitted aligned payload RAM");
                assert (remaining != 0 && (chunk == 1 || chunk == 4))
                    else $fatal(1, "DMA offered an empty/invalid chunk");
                assert (state == WAIT_READ ? m_wstrb == 0 :
                        (chunk == 4 ? m_wstrb == 15 : $onehot(m_wstrb)))
                    else $fatal(1, "DMA chunk strobes are inconsistent");
            end
            assert (!(done && busy)) else $fatal(1, "DMA terminal status asserted while busy");
        end
    end
`endif
endmodule
