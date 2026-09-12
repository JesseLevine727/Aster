## PYNQ-Z1 Rev C PL-only target
##
## The board's onboard USB-UART is wired to Zynq PS MIO. The PL UART is
## therefore exposed on Pmod JA[0] (Y18) for this first bring-up image.

set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports sysclk]
create_clock -name sysclk -period 8.000 [get_ports sysclk]
create_generated_clock -name core_clk -source [get_ports sysclk] -divide_by 4 [get_pins u_core_clk/O]

set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33 PULLDOWN true} [get_ports reset_btn]

set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports uart_tx]

set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## Asynchronous pushbutton assertion terminates at the reset synchronizer;
## deassertion is synchronized in RTL. UART/LED outputs have no external
## synchronous capture clock, so no synchronous I/O delay is applicable.
set_false_path -from [get_ports reset_btn]
set_false_path -to [get_ports {uart_tx led[*]}]
