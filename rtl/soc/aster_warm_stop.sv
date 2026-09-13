// RAM-preserving lifecycle: quiesce admissions, drain accepted transactions,
// allow the front-end completion handshake to settle, flush selected banks,
// then reset those cores. Power/configuration reset remains destructive.
`timescale 1 ns / 1 ps
module aster_warm_stop #(
    parameter int unsigned HART_COUNT = 2
) (
    input logic clk,
    input logic resetn,
    input logic host_run,
    input logic secondary_run,
    input logic fabric_busy,
    input logic flush_ready,
    output logic [1:0] hart_run,
    output logic [1:0] admit,
    output logic flush_valid,
    output logic [1:0] flush_mask,
    output logic [1:0] stop_commit,
    output logic stop_busy,
    output logic stopped
);
    typedef enum logic [2:0] {IDLE, DRAIN, SETTLE1, SETTLE2, FLUSH} state_t;
    state_t state;
    logic [1:0] running, selected;
    logic stop_requested;
    assign hart_run = resetn ? running : 2'b0;
    assign stop_requested = (|running && !host_run) || (running[1] && !secondary_run);
    // Even a selective stop temporarily stalls BOTH harts' new memory
    // admissions. It never resets the peer and avoids flush/AMO interleaving.
    assign admit = resetn && state == IDLE && !stop_requested && host_run ? running : 2'b0;
    assign flush_valid = resetn && state == FLUSH;
    assign flush_mask = selected;
    assign stop_commit = flush_valid && flush_ready ? selected : 2'b0;
    assign stop_busy = resetn && (state != IDLE || stop_requested);
    assign stopped = resetn && state == IDLE && running == 0;

    initial begin
        if (HART_COUNT != 1 && HART_COUNT != 2) $error("HART_COUNT must be 1 or 2");
    end
    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            running <= 0;
            selected <= 0;
        end else begin
            case (state)
                IDLE: begin
                    if (|running && !host_run) begin
                        selected <= 2'b11;
                        state <= DRAIN;
                    end else if (running[1] && !secondary_run) begin
                        selected <= 2'b10;
                        state <= DRAIN;
                    end else if (host_run && !running[0]) running <= 2'b01;
                    else if (host_run && secondary_run && HART_COUNT == 2) running[1] <= 1;
                end
                DRAIN: if (!fabric_busy) state <= SETTLE1;
                // Fabric RESPONSE reaches PCPI one edge before the core takes
                // pcpi_ready. Two quiet edges drain that owned boundary without
                // waiting for unadmitted requests that are about to be canceled.
                SETTLE1: state <= fabric_busy ? DRAIN : SETTLE2;
                SETTLE2: state <= fabric_busy ? DRAIN : FLUSH;
                FLUSH: if (flush_ready) begin
                    running <= running & ~selected;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
