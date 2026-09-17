// Phase 9.3 / v1.2: RAM-backed INT8 GEMM tile engine.
// This module is deliberately independent of the CPU/register boundary. It
// consumes one captured descriptor, drives the coherent-service memory port,
// and uses the parameterized ROWS x COLS array for each output tile. The
// default 4x4 geometry is the frozen v1.0/v1.1 behavior.
`timescale 1 ns / 1 ps
module aster_npu_engine #(
    parameter int ROWS = 4,
    parameter int COLS = 4
) (
    input  logic               clk,
    input  logic               resetn,
    input  logic               start,
    input  logic [31:0]        start_a_base,
    input  logic [31:0]        start_b_base,
    input  logic [31:0]        start_c_base,
    input  logic [31:0]        start_a_stride,
    input  logic [31:0]        start_b_stride,
    input  logic [31:0]        start_c_stride,
    input  logic [31:0]        start_m,
    input  logic [31:0]        start_n,
    input  logic [31:0]        start_k,
    input  logic               pause,
    input  logic               abort_request,
    input  logic               ack,
    output logic               busy,
    output logic               done,
    output logic               error,
    output logic               aborted,
    output logic [31:0]        error_code,
    output logic [31:0]        bytes_read,
    output logic [31:0]        bytes_written,
    output logic [63:0]        job_cycles,
    output logic [63:0]        compute_cycles,
    output logic [31:0]        tiles,
    output logic [4:0]         status,
    output logic               m_valid,
    output logic               m_write,
    output logic [31:0]        m_addr,
    output logic [31:0]        m_wdata,
    output logic [3:0]         m_wstrb,
    input  logic               m_ready,
    input  logic [31:0]        m_rdata
);
    localparam logic [31:0] RAM_LO = 32'h1000_0000;
    localparam logic [31:0] RAM_HI = 32'h1000_8000;
    localparam int ROW_BITS = ROWS > 1 ? $clog2(ROWS) : 1;
    localparam int COL_BITS = COLS > 1 ? $clog2(COLS) : 1;
    localparam int PROD_BITS = (ROWS*COLS) > 1 ? $clog2(ROWS*COLS) : 1;
    localparam int LOAD_BITS = $clog2((ROWS > COLS ? ROWS : COLS) + 1);
    localparam int OUT_BITS = $clog2(ROWS*COLS + 1);

    typedef enum logic [3:0] {
        IDLE,
        CHECK,
        VERIFY,
        ARRAY_START,
        LOAD_A,
        LOAD_B,
        ARRAY_STEP,
        ARRAY_FINISH,
        WAIT_ARRAY,
        WRITE_C
    } state_t;
    state_t state;

    logic [31:0] a_base, b_base, c_base;
    logic [31:0] a_stride, b_stride, c_stride;
    logic [31:0] rows, cols, reduction;
    logic [31:0] tile_row, tile_col, k_index;
    logic [LOAD_BITS-1:0] load_index;
    logic [OUT_BITS-1:0] output_index;
    logic [2:0] output_byte;
    logic signed [7:0] a_values [0:ROWS-1];
    logic signed [7:0] b_values [0:COLS-1];
    logic abort_pending, request_held;

    logic done_flag, error_flag, aborted_flag;
    logic start_accept;
    logic [31:0] descriptor_error;
    logic [63:0] a_byte_address, b_byte_address, c_byte_address;
    logic [63:0] load_index_ext, output_index_ext, output_byte_ext;
    logic [7:0] a_load_byte, b_load_byte;
    logic current_a_active, current_b_active, current_c_active;
    logic memory_state, memory_accept;
    logic read_accept, write_accept;
    logic tile_write_started, abort_drain_tile;

    logic array_resetn;
    logic array_start, array_start_ready, array_start_accept;
    logic array_step, array_step_accept;
    logic array_finish, array_finish_accept, array_result_valid;
    logic array_step_ready, array_finish_ready;
    logic signed [7:0] array_a [0:ROWS-1];
    logic signed [7:0] array_b [0:COLS-1];
    logic [ROWS-1:0] array_row_mask;
    logic [COLS-1:0] array_col_mask;
    logic [31:0] array_results [0:ROWS*COLS-1];

    // The child is reset whenever this engine is idle. This clears an
    // interrupted tile without adding a second externally-visible lifecycle.
    assign array_resetn = resetn && state != IDLE;
    assign array_start = resetn && state == ARRAY_START && !pause && !abort_pending && !abort_request && array_start_ready;
    assign array_step = resetn && state == ARRAY_STEP && !pause && !abort_pending && !abort_request && array_step_ready;
    assign array_finish = resetn && state == ARRAY_FINISH && !pause && !abort_pending && !abort_request && array_finish_ready;

    aster_int8_array #(.ROWS(ROWS), .COLS(COLS)) array (
        .clk(clk),
        .resetn(array_resetn),
        .start_tile(array_start),
        .start_ready(array_start_ready),
        .start_accept(array_start_accept),
        .step_valid(array_step),
        .step_ready(array_step_ready),
        .step_accept(array_step_accept),
        .a_row(array_a),
        .b_col(array_b),
        .row_mask(array_row_mask),
        .col_mask(array_col_mask),
        .finish_tile(array_finish),
        .finish_ready(array_finish_ready),
        .finish_accept(array_finish_accept),
        .result_valid(array_result_valid),
        .results(array_results)
    );

    assign busy = resetn && state != IDLE;
    assign done = resetn && done_flag;
    assign error = resetn && error_flag;
    assign aborted = resetn && aborted_flag;
    assign status = {busy, aborted, error, done, busy};
    assign start_accept = resetn && start && state == IDLE && !pause && !abort_request;

    // Request payload is derived only from captured state. request_held keeps a
    // stalled offer alive through pause/abort, as required by the device port.
    assign memory_state = state == LOAD_A || state == LOAD_B || state == WRITE_C;
    assign m_write = state == WRITE_C;
    assign m_valid = resetn && memory_state &&
        (request_held || (!pause && !abort_pending && !abort_request) ||
         (abort_drain_tile && state == WRITE_C)) &&
        (state == LOAD_A ? current_a_active :
         state == LOAD_B ? current_b_active : current_c_active);
    assign memory_accept = m_valid && m_ready;
    assign read_accept = memory_accept && !m_write;
    assign write_accept = memory_accept && m_write;

    assign current_a_active = load_index < LOAD_BITS'(ROWS) &&
                              (tile_row + 32'(load_index)) < rows;
    assign current_b_active = load_index < LOAD_BITS'(COLS) &&
                              (tile_col + 32'(load_index)) < cols;
    assign current_c_active = (tile_row + (32'(output_index) >> COL_BITS)) < rows &&
                              (tile_col + (32'(output_index) & (32'(COLS) - 32'd1))) < cols &&
                              output_index < OUT_BITS'(ROWS*COLS);
    always_comb begin
        for (int r = 0; r < ROWS; r++) array_row_mask[r] = (tile_row + 32'(r)) < rows;
        for (int c = 0; c < COLS; c++) array_col_mask[c] = (tile_col + 32'(c)) < cols;
    end
    assign array_a = a_values;
    assign array_b = b_values;

    assign load_index_ext = {{(64-LOAD_BITS){1'b0}}, load_index};
    assign output_index_ext = {{(64-OUT_BITS){1'b0}}, output_index};
    assign output_byte_ext = {{61{1'b0}}, output_byte};
    assign a_load_byte = m_rdata[(8 * a_byte_address[1:0]) +: 8];
    assign b_load_byte = m_rdata[(8 * b_byte_address[1:0]) +: 8];

    assign a_byte_address = {32'd0, a_base} +
                            ({32'd0, tile_row} + load_index_ext) * {32'd0, a_stride} +
                            {32'd0, k_index};
    assign b_byte_address = {32'd0, b_base} +
                            {32'd0, k_index} * {32'd0, b_stride} +
                            ({32'd0, tile_col} + load_index_ext);
    assign c_byte_address = {32'd0, c_base} +
                            ({32'd0, tile_row} + (output_index_ext >> COL_BITS)) * {32'd0, c_stride} +
                            (({32'd0, tile_col} + (output_index_ext & (64'(COLS) - 64'd1))) * 64'd4) +
                            output_byte_ext;

    always_comb begin
        m_addr = 0;
        m_wdata = 0;
        m_wstrb = 0;
        if (state == LOAD_A) begin
            m_addr = {a_byte_address[31:2], 2'b00} | {32{(|a_byte_address[63:32])}};
        end else if (state == LOAD_B) begin
            m_addr = {b_byte_address[31:2], 2'b00} | {32{(|b_byte_address[63:32])}};
        end else if (state == WRITE_C) begin
            m_addr = {c_byte_address[31:2], 2'b00} | {32{(|c_byte_address[63:32])}};
            m_wstrb = 4'b0001 << c_byte_address[1:0];
            m_wdata = ((array_results[output_index[PROD_BITS-1:0]] >> (8 * output_byte)) & 32'hff) <<
                      (8 * c_byte_address[1:0]);
        end
    end

    // Widened descriptor arithmetic prevents wraparound before the first
    // memory request. Error values are stable software-visible ABI values.
    logic [63:0] a_end, b_end, c_end;
    logic [63:0] a_end_q, b_end_q, c_end_q;
    logic a_nonempty, b_nonempty, c_nonempty;
    // Ends are computed from the latched descriptor and registered, so the long
    // multiply-add no longer sits on the descriptor-error/clock-enable path.
    always_comb begin
        a_nonempty = rows != 0 && reduction != 0;
        b_nonempty = cols != 0 && reduction != 0;
        c_nonempty = rows != 0 && cols != 0;
        a_end = {32'd0, a_base};
        b_end = {32'd0, b_base};
        c_end = {32'd0, c_base};
        if (a_nonempty) a_end = {32'd0, a_base} +
            {32'd0, rows - 1} * {32'd0, a_stride} + {32'd0, reduction};
        if (b_nonempty) b_end = {32'd0, b_base} +
            {32'd0, reduction - 1} * {32'd0, b_stride} + {32'd0, cols};
        if (c_nonempty) c_end = {32'd0, c_base} +
            {32'd0, rows - 1} * {32'd0, c_stride} +
            ({32'd0, cols} - 64'd1) * 64'd4 + 64'd4;
    end
    // Error decode runs in VERIFY from the registered ends.
    always_comb begin
        descriptor_error = 0;
        if (rows > 1024 || cols > 1024 || reduction > 1024)
            descriptor_error = 1;
        else if (a_stride < reduction || b_stride < cols || c_stride < cols * 4)
            descriptor_error = 2;
        else if (a_nonempty && (a_base < RAM_LO || a_end_q > {32'd0, RAM_HI}))
            descriptor_error = 3;
        else if (b_nonempty && (b_base < RAM_LO || b_end_q > {32'd0, RAM_HI}))
            descriptor_error = 4;
        else if (c_nonempty && (c_base < RAM_LO || c_end_q > {32'd0, RAM_HI}))
            descriptor_error = 5;
        else if (a_nonempty && b_nonempty &&
                 {32'd0, a_base} < b_end_q && {32'd0, b_base} < a_end_q)
            descriptor_error = 6;
        else if (a_nonempty && c_nonempty &&
                 {32'd0, a_base} < c_end_q && {32'd0, c_base} < a_end_q)
            descriptor_error = 6;
        else if (b_nonempty && c_nonempty &&
                 {32'd0, b_base} < c_end_q && {32'd0, c_base} < b_end_q)
            descriptor_error = 6;
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            a_base <= 0; b_base <= 0; c_base <= 0;
            a_stride <= 0; b_stride <= 0; c_stride <= 0;
            rows <= 0; cols <= 0; reduction <= 0;
            tile_row <= 0; tile_col <= 0; k_index <= 0;
            load_index <= 0; output_index <= 0; output_byte <= 0;
            abort_pending <= 0; request_held <= 0;
            tile_write_started <= 0; abort_drain_tile <= 0;
            done_flag <= 0; error_flag <= 0; aborted_flag <= 0; error_code <= 0;
            a_end_q <= 0; b_end_q <= 0; c_end_q <= 0;
            bytes_read <= 0; bytes_written <= 0; job_cycles <= 0;
            compute_cycles <= 0; tiles <= 0;
            for (int i = 0; i < ROWS; i++) a_values[i] <= 0;
            for (int i = 0; i < COLS; i++) b_values[i] <= 0;
        end else begin
            if (busy) job_cycles <= job_cycles + 64'd1;
            if (busy && abort_request) abort_pending <= 1'b1;

            if (m_valid && !m_ready) request_held <= 1'b1;
            else if (m_valid && m_ready) request_held <= 1'b0;
            else if (!memory_state) request_held <= 1'b0;

            if (read_accept) bytes_read <= bytes_read + 1;
            if (write_accept) bytes_written <= bytes_written + 1;
            if (array_step_accept) begin
                compute_cycles <= compute_cycles + 64'd1;
            end

            case (state)
                IDLE: begin
                    if (ack && !busy) begin
                        done_flag <= 0;
                        error_flag <= 0;
                        aborted_flag <= 0;
                        error_code <= 0;
                    end
                    if (start_accept) begin
                        a_base <= start_a_base; b_base <= start_b_base; c_base <= start_c_base;
                        a_stride <= start_a_stride; b_stride <= start_b_stride; c_stride <= start_c_stride;
                        rows <= start_m; cols <= start_n; reduction <= start_k;
                        tile_row <= 0; tile_col <= 0; k_index <= 0;
                        load_index <= 0; output_index <= 0; output_byte <= 0;
                        abort_pending <= 0; done_flag <= 0; error_flag <= 0; aborted_flag <= 0;
                        error_code <= 0; bytes_read <= 0; bytes_written <= 0;
                        job_cycles <= 0; compute_cycles <= 0; tiles <= 0;
                        tile_write_started <= 0; abort_drain_tile <= 0;
                        state <= CHECK;
                    end
                end
                CHECK: begin
                    a_end_q <= a_end;
                    b_end_q <= b_end;
                    c_end_q <= c_end;
                    state <= VERIFY;
                end
                VERIFY: begin
                    if (descriptor_error != 0) begin
                        state <= IDLE; done_flag <= 1; error_flag <= 1;
                        error_code <= descriptor_error;
                    end else if (rows == 0 || cols == 0) begin
                        state <= IDLE; done_flag <= 1;
                    end else begin
                        state <= ARRAY_START;
                        for (int i = 0; i < ROWS; i++) a_values[i] <= 0;
                        for (int i = 0; i < COLS; i++) b_values[i] <= 0;
                    end
                end
                ARRAY_START: if (array_start_accept) begin
                    load_index <= 0;
                    tile_write_started <= 0;
                    abort_drain_tile <= 0;
                    if (reduction == 0) state <= ARRAY_FINISH;
                    else state <= LOAD_A;
                end
                LOAD_A: begin
                    if (load_index >= LOAD_BITS'(ROWS)) begin
                        load_index <= 0;
                        state <= LOAD_B;
                    end else if (!current_a_active) begin
                        load_index <= load_index + 1;
                    end else if (read_accept) begin
                        a_values[load_index[ROW_BITS-1:0]] <= a_load_byte;
                        load_index <= load_index + 1;
                    end
                end
                LOAD_B: begin
                    if (load_index >= LOAD_BITS'(COLS)) begin
                        load_index <= 0;
                        state <= ARRAY_STEP;
                    end else if (!current_b_active) begin
                        load_index <= load_index + 1;
                    end else if (read_accept) begin
                        b_values[load_index[COL_BITS-1:0]] <= b_load_byte;
                        load_index <= load_index + 1;
                    end
                end
                ARRAY_STEP: if (array_step_accept) begin
                    if (k_index + 1 >= reduction) begin
                        state <= ARRAY_FINISH;
                    end else begin
                        k_index <= k_index + 1;
                        load_index <= 0;
                        state <= LOAD_A;
                    end
                end
                ARRAY_FINISH: if (array_finish_accept) state <= WAIT_ARRAY;
                WAIT_ARRAY: if (array_result_valid) begin
                    output_index <= 0;
                    output_byte <= 0;
                    state <= WRITE_C;
                end
                WRITE_C: begin
                    if (output_index >= OUT_BITS'(ROWS*COLS)) begin
                        tiles <= tiles + 1;
                        k_index <= 0;
                        if (tile_col + 32'(COLS) >= cols) begin
                            tile_col <= 0;
                            if (tile_row + 32'(ROWS) >= rows) begin
                                state <= IDLE;
                                done_flag <= 1;
                            end else begin
                                tile_row <= tile_row + 32'(ROWS);
                                state <= ARRAY_START;
                            end
                        end else begin
                            tile_col <= tile_col + 32'(COLS);
                            state <= ARRAY_START;
                        end
                    end else if (!current_c_active) begin
                        output_index <= output_index + 1;
                        output_byte <= 0;
                    end else if (write_accept) begin
                        tile_write_started <= 1;
                        if (output_byte == 3) begin
                            output_byte <= 0;
                            output_index <= output_index + 1;
                        end else begin
                            output_byte <= output_byte + 1;
                        end
                    end
                end
                default: state <= IDLE;
            endcase

            // ABORT is cooperative: an already-offered memory transaction is
            // allowed to settle. If C emission has begun, the current tile is
            // drained so an abort cannot expose a partial output tile; no next
            // tile is started and no full result is claimed.
            if (busy && (abort_pending || abort_request) && (!m_valid || m_ready)) begin
                if (state == WRITE_C && output_index < OUT_BITS'(ROWS*COLS) &&
                    (tile_write_started || write_accept)) begin
                    abort_drain_tile <= 1;
                end else begin
                    state <= IDLE;
                    done_flag <= 1;
                    aborted_flag <= 1;
                    abort_pending <= 0;
                    abort_drain_tile <= 0;
                end
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
                else $fatal(1, "NPU withdrew or changed an offered transaction");
            held <= m_valid && !m_ready;
            held_addr <= m_addr; held_data <= m_wdata; held_strobes <= m_wstrb;
            if (m_valid) begin
                assert (m_addr >= RAM_LO && m_addr < RAM_HI && m_addr[1:0] == 0)
                    else $fatal(1, "NPU escaped permitted aligned shared RAM");
                assert (m_write ? $onehot(m_wstrb) : m_wstrb == 0)
                    else $fatal(1, "NPU read/write strobes are invalid");
            end
            assert (!(done && busy)) else $fatal(1, "NPU terminal status asserted while busy");
        end
    end
`endif
endmodule
