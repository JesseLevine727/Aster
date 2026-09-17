// PYNQ Linux host bridge. ARM loads boot ROM while Aster is held in reset,
// then starts RV32IM or the explicitly selected coherent RV32IMA SoC and reads
// decoded serial output via AXI. Phase 6 separates safe host stop from POR.
module aster_pynq_linux #(
    // 0 preserves the legacy Phase 2 map/ABI; 1/2 select the Phase 5 map.
    parameter int unsigned HART_COUNT = 0,
    parameter bit ENABLE_COHERENCE = 1'b0,
    parameter bit COHERENT_L1 = 1'b1,
    parameter bit ENABLE_DMA = 1'b0,
    parameter bit ENABLE_DOT8 = 1'b0,
    parameter bit ENABLE_NPU = 1'b0,
    parameter bit ENABLE_L2 = 1'b0,
    parameter int unsigned L2_LINE_WORDS = 4,
    parameter int unsigned L2_LINE_COUNT = 64,
    parameter int unsigned CLK_HZ = 31_250_000,
    parameter int unsigned BAUD = 115_200,
    parameter int unsigned RX_DEPTH = 512
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET aresetn, FREQ_HZ 31250000" *)
    input logic aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input logic aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR",
       X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 18" *)
    input logic [17:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input logic s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output logic s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input logic [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input logic [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input logic s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output logic s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output logic [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output logic s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input logic s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input logic [17:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input logic s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output logic s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output logic [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output logic s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input logic s_axi_rready,
    output logic uart_tx,
    output logic [3:0] led
);
    localparam int TX_DEPTH = 64;
    localparam int PTR_BITS = $clog2(RX_DEPTH);
    localparam int COUNT_BITS = $clog2(RX_DEPTH + 1);
    logic run;
    logic [1:0] run_pipe;
    logic coherent_stopped, coherent_stop_busy, coherent_flush;
    logic [1:0] atomic_fault_valid;
    logic [3:0] atomic_fault_cause [0:1];
    logic [31:0] atomic_fault_addr [0:1], atomic_fault_insn [0:1], hart_pc [0:1];
    logic [31:0] host_ram_rdata;
    // Simulation observation points; unused in the packaged physical shell.
    logic coherent_store_commit, coherent_store_owner;
    logic [31:0] coherent_store_addr, coherent_store_data;
    logic [3:0] coherent_store_mask;
    logic coherent_perf_start, coherent_perf_freeze, coherent_perf_resume;
    logic [13:0] coherent_perf_events [0:1];
    logic [4:0] dma_status;
    logic [31:0] dma_bytes_done, dma_error_code;
    logic [63:0] dma_job_cycles, dma_counters [0:13];
    logic dma_counting, dma_busy, dma_request_pending, dma_request_ready, dma_store_commit;
    logic [31:0] dma_request_addr, dma_request_data, dma_request_rdata;
    logic [3:0] dma_request_mask;
    logic [2:0] dma_events [0:13];
    logic dma_backing_valid, dma_backing_ready, dma_backing_device, dma_backing_owner;
    logic [31:0] dma_backing_addr, dma_backing_data;
    logic [3:0] dma_backing_mask;
    logic [1:0] dot8_busy;
    logic [3:0] dot8_events [0:1];
    logic [63:0] dot8_counters [0:7];
    logic dot8_counting;
    logic npu_busy, npu_done, npu_error, npu_aborted;
    logic [4:0] npu_status;
    logic [31:0] npu_error_code, npu_bytes_read, npu_bytes_written, npu_tiles;
    logic [63:0] npu_job_cycles, npu_compute_cycles;
    logic ram_read_pending;
    logic [15:0] ram_read_addr;
    wire [15:0] host_ram_addr = ram_read_pending ? ram_read_addr : s_axi_araddr[15:0];
    wire cpu_reset_n = aresetn && (ENABLE_COHERENCE ? !coherent_stopped : run && run_pipe[1]);
    logic aw_held, w_held;
    logic [17:0] awaddr;
    logic [31:0] wdata;
    logic [3:0] wstrb;
    wire write_fire = aw_held && w_held && !s_axi_bvalid;
    wire boot_address = awaddr >= 18'h10000 && awaddr < 18'h20000;
    wire boot_allowed = !run && (!ENABLE_COHERENCE || coherent_stopped);
    wire boot_we = aresetn && write_fire && boot_address && boot_allowed && awaddr[1:0] == 0;
    wire read_fire = s_axi_arvalid && s_axi_arready;

    logic core_tx_valid, core_tx_ready, phy_ready, phy_busy, trap;
    logic [1:0] hart_run, hart_trap, hart_retired;
    logic [63:0] retirement [0:1];
    assign trap = ENABLE_COHERENCE ? |(hart_trap & hart_run) : |hart_trap;
    logic [7:0] core_tx_data;
    logic rx_valid, rx_framing_error;
    logic [7:0] rx_data;
    logic [7:0] fifo [0:RX_DEPTH-1];
    logic [PTR_BITS-1:0] read_ptr, write_ptr;
    logic [COUNT_BITS-1:0] count;
    logic [31:0] transmitted, received;
    logic overflow, framing_error;
    wire pop = read_fire && s_axi_araddr == 18'h8 && count != 0;
    wire push = rx_valid && count < COUNT_BITS'(RX_DEPTH);
    // Reserve space for every byte in the TX FIFO, active frame, elastic
    // UART slot and receiver pipeline. Host pauses cannot overflow RX.
    wire receive_room = count < COUNT_BITS'(RX_DEPTH - TX_DEPTH - 4);
    assign core_tx_ready = phy_ready && receive_room;

    initial begin
        if (HART_COUNT > 2) $error("Linux HART_COUNT must be 0 (legacy), 1 or 2");
        if (ENABLE_COHERENCE && HART_COUNT == 0) $error("coherent Linux requires one or two harts");
        if (ENABLE_DMA && !ENABLE_COHERENCE) $error("Linux DMA requires the coherent SoC");
        if (ENABLE_DOT8 && (!ENABLE_COHERENCE || !ENABLE_DMA)) $error("Linux dot8 requires coherence and DMA");
        if (RX_DEPTH < 128 || (RX_DEPTH & (RX_DEPTH-1)) != 0)
            $error("RX_DEPTH must be a power of two >= 128");
    end

    assign s_axi_awready = aresetn && !aw_held && !s_axi_bvalid;
    assign s_axi_wready = aresetn && !w_held && !s_axi_bvalid;
    assign s_axi_arready = aresetn && !s_axi_rvalid && !ram_read_pending;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            run <= 0;
            run_pipe <= 0;
            aw_held <= 0;
            w_held <= 0;
            awaddr <= 0;
            wdata <= 0;
            wstrb <= 0;
            s_axi_bvalid <= 0;
            s_axi_bresp <= 0;
            s_axi_rvalid <= 0;
            s_axi_rresp <= 0;
            s_axi_rdata <= 0;
            ram_read_pending <= 0;
            ram_read_addr <= 0;
        end else begin
            run_pipe <= {run_pipe[0], run};
            if (s_axi_awvalid && s_axi_awready) begin awaddr <= s_axi_awaddr; aw_held <= 1; end
            if (s_axi_wvalid && s_axi_wready) begin
                wdata <= s_axi_wdata; wstrb <= s_axi_wstrb; w_held <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (write_fire) begin
                aw_held <= 0;
                w_held <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                if (awaddr == 0) begin
                    if (wstrb[0]) begin
                        if (ENABLE_COHERENCE && wdata[0] && !run && !coherent_stopped)
                            s_axi_bresp <= 2'b10; // Cannot cancel a mandatory drain/flush.
                        else run <= wdata[0];
                    end
                end else if (!boot_address || !boot_allowed || awaddr[1:0] != 0) begin
                    s_axi_bresp <= 2'b10;
                end
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (ram_read_pending) begin
                ram_read_pending <= 0;
                s_axi_rvalid <= 1;
                s_axi_rresp <= coherent_stopped && !run ? 2'b00 : 2'b10;
                s_axi_rdata <= coherent_stopped && !run ? host_ram_rdata : 32'b0;
            end
            if (read_fire) begin
                s_axi_rresp <= 0;
                if (ENABLE_COHERENCE && s_axi_araddr >= 18'h20000 && s_axi_araddr < 18'h30000 &&
                    coherent_stopped && !run && s_axi_araddr[1:0] == 0) begin
                    // One additional edge captures the actual synchronous RAM
                    // output; response remains stable under AXI backpressure.
                    ram_read_pending <= 1;
                    ram_read_addr <= s_axi_araddr[15:0];
                end else begin
                    s_axi_rvalid <= 1;
                    case (s_axi_araddr)
                    18'h00: s_axi_rdata <= {31'd0, run};
                    18'h04: s_axi_rdata <= {27'd0, framing_error, overflow, phy_busy, trap, cpu_reset_n};
                    18'h08: s_axi_rdata <= count != 0 ? {1'b1, 23'd0, fifo[read_ptr]} : 32'd0;
                    18'h0c: s_axi_rdata <= 32'(count);
                    18'h10: s_axi_rdata <= transmitted;
                    18'h14: s_axi_rdata <= received;
                    18'h18: s_axi_rdata <= 32'h41535452; // ASTR
                    18'h1c: s_axi_rdata <= ENABLE_NPU ? 32'h00090001 : ENABLE_DOT8 ? 32'h00080001 : ENABLE_DMA ? 32'h00070001 : ENABLE_COHERENCE ? 32'h00060001 :
                        HART_COUNT == 0 ? 32'h00020001 : 32'h00050001;
                    18'h20: s_axi_rdata <= CLK_HZ;
                    18'h24, 18'h28, 18'h30, 18'h34, 18'h38, 18'h3c: begin
                        if (HART_COUNT == 0) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                        else case (s_axi_araddr)
                            18'h24: s_axi_rdata <= HART_COUNT;
                            18'h28: s_axi_rdata <= {22'd0, hart_trap & hart_run, 6'd0, hart_run};
                            18'h30: s_axi_rdata <= retirement[0][31:0];
                            18'h34: s_axi_rdata <= retirement[0][63:32];
                            18'h38: s_axi_rdata <= retirement[1][31:0];
                            18'h3c: s_axi_rdata <= retirement[1][63:32];
                            default: s_axi_rdata <= 0;
                        endcase
                    end
                    18'h40, 18'h44, 18'h50, 18'h54, 18'h58, 18'h5c,
                    18'h60, 18'h64, 18'h68, 18'h6c: begin
                        if (!ENABLE_COHERENCE) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                        else case (s_axi_araddr)
                            18'h40: s_axi_rdata <= {29'b0, coherent_flush, coherent_stop_busy, coherent_stopped};
                            18'h44: s_axi_rdata <= {27'b0, ENABLE_NPU, ENABLE_DOT8, ENABLE_DMA, COHERENT_L1, 1'b1};
                            18'h50, 18'h60: s_axi_rdata <= {27'b0, atomic_fault_valid[s_axi_araddr[5]], atomic_fault_cause[s_axi_araddr[5]]};
                            18'h54, 18'h64: s_axi_rdata <= atomic_fault_addr[s_axi_araddr[5]];
                            18'h58, 18'h68: s_axi_rdata <= atomic_fault_insn[s_axi_araddr[5]];
                            18'h5c, 18'h6c: s_axi_rdata <= hart_pc[s_axi_araddr[5]];
                            default: s_axi_rdata <= 0;
                        endcase
                    end
                    18'h80, 18'h84, 18'h88, 18'h8c, 18'h90, 18'h94, 18'h98, 18'h9c: begin
                        if (!ENABLE_DMA) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                        else case (s_axi_araddr)
                            18'h80: s_axi_rdata <= 1;
                            18'h84: s_axi_rdata <= {27'b0, dma_status};
                            18'h88: s_axi_rdata <= dma_bytes_done;
                            18'h8c: s_axi_rdata <= dma_error_code;
                            18'h90: s_axi_rdata <= dma_job_cycles[31:0];
                            18'h94: s_axi_rdata <= dma_job_cycles[63:32];
                            18'h98: s_axi_rdata <= {31'b0, dma_counting};
                            18'h9c: s_axi_rdata <= 5;
                            default: s_axi_rdata <= 0;
                        endcase
                    end
                    18'h110, 18'h114, 18'h118, 18'h11c, 18'h160, 18'h164, 18'h168: begin
                        if (!ENABLE_DOT8) begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                        else case (s_axi_araddr)
                            18'h110: s_axi_rdata <= 1;
                            18'h114: s_axi_rdata <= 6;
                            18'h118: s_axi_rdata <= {31'b0, dot8_counting};
                            18'h11c: s_axi_rdata <= {30'b0, dot8_busy};
                            18'h160: s_axi_rdata <= 32'h0000_000b;
                            18'h164: s_axi_rdata <= 32'hfe00_707f;
                            18'h168: s_axi_rdata <= 4;
                            default: s_axi_rdata <= 0;
                        endcase
                    end
                    default: begin
                        if (ENABLE_DMA && s_axi_araddr >= 18'ha0 && s_axi_araddr < 18'h110 && s_axi_araddr[1:0] == 0)
                            s_axi_rdata <= s_axi_araddr[2] ? dma_counters[4'((s_axi_araddr-18'ha0) >> 3)][63:32] :
                                dma_counters[4'((s_axi_araddr-18'ha0) >> 3)][31:0];
                        else if (ENABLE_DOT8 && s_axi_araddr >= 18'h120 && s_axi_araddr < 18'h160 && s_axi_araddr[1:0] == 0)
                            s_axi_rdata <= s_axi_araddr[2] ? dot8_counters[3'((s_axi_araddr-18'h120) >> 3)][63:32] :
                                dot8_counters[3'((s_axi_araddr-18'h120) >> 3)][31:0];
                        else begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                    end
                    endcase
                end
            end
        end
    end

    always_ff @(posedge aclk) begin
        if (!cpu_reset_n) begin
            read_ptr <= 0; write_ptr <= 0; count <= 0;
            transmitted <= 0; received <= 0;
            overflow <= 0; framing_error <= 0;
        end else begin
            if (core_tx_valid && core_tx_ready) transmitted <= transmitted + 1'b1;
            if (rx_framing_error) framing_error <= 1;
            if (rx_valid && !push) overflow <= 1;
            if (push) begin
                fifo[write_ptr] <= rx_data;
                write_ptr <= write_ptr + 1'b1;
                received <= received + 1'b1;
            end
            if (pop) read_ptr <= read_ptr + 1'b1;
            case ({push, pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: begin end
            endcase
        end
    end

    for (genvar h = 0; h < 2; h++) begin : g_retirement
        always_ff @(posedge aclk) begin
            if (!cpu_reset_n) retirement[h] <= 0;
            else if (hart_retired[h]) retirement[h] <= retirement[h] + 1'b1;
        end
    end
    generate if (!ENABLE_COHERENCE) begin : g_no_coherent_status
        assign coherent_stopped = 0;
        assign coherent_stop_busy = 0;
        assign coherent_flush = 0;
        assign atomic_fault_valid = 0;
        assign host_ram_rdata = 0;
        assign coherent_store_commit = 0;
        assign coherent_store_owner = 0;
        assign coherent_store_addr = 0;
        assign coherent_store_data = 0;
        assign coherent_store_mask = 0;
        assign coherent_perf_start = 0;
        assign coherent_perf_freeze = 0;
        assign coherent_perf_resume = 0;
        assign dma_status = 0;
        assign dma_bytes_done = 0;
        assign dma_error_code = 0;
        assign dma_job_cycles = 0;
        assign dma_counting = 0;
        assign dma_busy = 0;
        assign dma_request_pending = 0;
        assign dma_request_ready = 0;
        assign dma_store_commit = 0;
        assign dma_request_addr = 0;
        assign dma_request_data = 0;
        assign dma_request_rdata = 0;
        assign dma_request_mask = 0;
        assign dma_backing_valid = 0;
        assign dma_backing_ready = 0;
        assign dma_backing_device = 0;
        assign dma_backing_owner = 0;
        assign dma_backing_addr = 0;
        assign dma_backing_data = 0;
        assign dma_backing_mask = 0;
        assign dot8_busy = 0;
        assign dot8_counting = 0;
        for (genvar i = 0; i < 8; i++) assign dot8_counters[i] = 0;
        for (genvar i = 0; i < 14; i++) begin : g_no_dma_events
            assign dma_events[i] = 0;
            assign dma_counters[i] = 0;
        end
        for (genvar h = 0; h < 2; h++) begin : g_hart_status
            assign atomic_fault_cause[h] = 0;
            assign atomic_fault_addr[h] = 0;
            assign atomic_fault_insn[h] = 0;
            assign hart_pc[h] = 0;
            assign coherent_perf_events[h] = 0;
            assign dot8_events[h] = 0;
        end
    end endgenerate
    generate if (HART_COUNT == 0) begin : g_legacy
        assign hart_run = {1'b0, cpu_reset_n};
        assign hart_trap[1] = 0;
        assign hart_retired = 0; // no lifetime-bank ABI in the legacy shell
        aster_minimal #(.SYNC_MEMORY(1'b1), .HOST_BOOT(1'b1), .CLOCK_HZ(CLK_HZ)) soc (
            .clk(aclk), .rst_n(cpu_reset_n),
            .uart_tx_valid(core_tx_valid), .uart_tx_data(core_tx_data),
            .uart_tx_ready(core_tx_ready), .trap(hart_trap[0]),
            .boot_we(boot_we), .boot_addr(awaddr[15:0]), .boot_wdata(wdata), .boot_wstrb(wstrb)
        );
    end else if (ENABLE_COHERENCE) begin : g_coherent
        /* verilator lint_off PINCONNECTEMPTY */
        aster_coherent_soc #(.HART_COUNT(HART_COUNT), .SYNC_MEMORY(1'b1), .HOST_BOOT(1'b1),
                            .ENABLE_L1(COHERENT_L1), .ENABLE_DMA(ENABLE_DMA), .ENABLE_DOT8(ENABLE_DOT8), .ENABLE_NPU(ENABLE_NPU), .CLOCK_HZ(CLK_HZ),
                            .ENABLE_L2(ENABLE_L2), .L2_LINE_WORDS(L2_LINE_WORDS), .L2_LINE_COUNT(L2_LINE_COUNT)) soc (
            .clk(aclk), .resetn(aresetn), .host_run(run && run_pipe[1]),
            .stopped(coherent_stopped), .stop_busy(coherent_stop_busy), .flush_active(coherent_flush),
            .uart_tx_valid(core_tx_valid), .uart_tx_data(core_tx_data), .uart_tx_ready(core_tx_ready),
            .hart_trap(hart_trap), .hart_run(hart_run), .retired(hart_retired), .retired_pc(hart_pc), .retired_insn(),
            .fault_valid(atomic_fault_valid), .fault_cause(atomic_fault_cause),
            .fault_addr(atomic_fault_addr), .fault_insn(atomic_fault_insn),
            .perf_start(coherent_perf_start), .perf_freeze(coherent_perf_freeze), .perf_resume(coherent_perf_resume),
            .perf_events(coherent_perf_events), .timer_irq(), .irq0(), .irq1(),
            .store_commit(coherent_store_commit), .store_owner(coherent_store_owner),
            .store_addr(coherent_store_addr), .store_data(coherent_store_data), .store_mask(coherent_store_mask),
            .fabric_busy(), .stop_commit(), .reservations(), .reservation_addr(),
            .backing_valid(dma_backing_valid), .backing_ready(dma_backing_ready), .backing_addr(dma_backing_addr), .backing_mask(dma_backing_mask),
            .atomic_active(), .atomic_read_commit(), .atomic_write_pending(),
            .backing_device(dma_backing_device), .backing_owner(dma_backing_owner), .backing_data(dma_backing_data), .dma_busy(dma_busy),
            .dma_request_pending(dma_request_pending), .dma_request_ready(dma_request_ready), .dma_request_addr(dma_request_addr),
            .dma_request_data(dma_request_data), .dma_request_rdata(dma_request_rdata), .dma_request_mask(dma_request_mask),
            .dma_status(dma_status), .dma_bytes_done(dma_bytes_done), .dma_error_code(dma_error_code), .dma_job_cycles(dma_job_cycles),
            .dma_store_commit(dma_store_commit), .dma_events(dma_events), .dma_counters(dma_counters), .dma_counting(dma_counting),
            .dot8_busy(dot8_busy), .dot8_events(dot8_events), .dot8_counters(dot8_counters), .dot8_counting(dot8_counting),
            .npu_busy(npu_busy), .npu_done(npu_done), .npu_error(npu_error), .npu_aborted(npu_aborted),
            .npu_status(npu_status), .npu_error_code(npu_error_code), .npu_bytes_read(npu_bytes_read),
            .npu_bytes_written(npu_bytes_written), .npu_job_cycles(npu_job_cycles),
            .npu_compute_cycles(npu_compute_cycles), .npu_tiles(npu_tiles),
            .boot_we(boot_we), .boot_addr(awaddr[15:0]), .boot_wdata(wdata), .boot_wstrb(wstrb),
            .host_ram_addr(host_ram_addr), .host_ram_rdata(host_ram_rdata)
        );
        /* verilator lint_on PINCONNECTEMPTY */
    end else begin : g_multicore
        /* verilator lint_off PINCONNECTEMPTY */
        aster_multicore #(.HART_COUNT(HART_COUNT), .SYNC_MEMORY(1'b1), .HOST_BOOT(1'b1), .CLOCK_HZ(CLK_HZ)) soc (
            .clk(aclk), .rst_n(cpu_reset_n),
            .uart_tx_valid(core_tx_valid), .uart_tx_data(core_tx_data), .uart_tx_ready(core_tx_ready),
            .hart_trap(hart_trap), .hart_run(hart_run), .retired(hart_retired), .retired_pc(),
            .perf_start(), .perf_freeze(), .perf_resume(), .memory_events(),
            .cache_access_events(), .cache_miss_events(), .backing_events(),
            .boot_we(boot_we), .boot_addr(awaddr[15:0]), .boot_wdata(wdata), .boot_wstrb(wstrb)
        );
        /* verilator lint_on PINCONNECTEMPTY */
    end endgenerate
    aster_uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .FIFO_DEPTH(TX_DEPTH)) transmitter (
        .clk(aclk), .rst_n(cpu_reset_n), .tx_valid_i(core_tx_valid && receive_room),
        .tx_data_i(core_tx_data), .ready_o(phy_ready), .tx_o(uart_tx), .busy_o(phy_busy)
    );
    aster_uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) receiver (
        .clk(aclk), .rst_n(cpu_reset_n), .rx_i(uart_tx),
        .valid_o(rx_valid), .data_o(rx_data), .framing_error_o(rx_framing_error)
    );
    assign led = {framing_error || overflow, trap, phy_busy, cpu_reset_n};
endmodule
