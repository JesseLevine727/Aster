module aster_smoke (
    input  logic       clk,
    input  logic       rst_n,
    output logic [7:0] counter,
    output logic       heartbeat
);
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            counter <= 8'd0;
            heartbeat <= 1'b0;
        end else begin
            counter <= counter + 8'd1;
            heartbeat <= ~heartbeat;
        end
    end
endmodule
