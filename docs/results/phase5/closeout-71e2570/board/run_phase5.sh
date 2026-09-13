#!/usr/bin/env bash
set -euo pipefail
cd /home/xilinx/aster_phase5_71e2570
python3 -c 'from pynq import PL, MMIO; print("PREVIOUS_BITSTREAM", PL.bitfile_name); m=MMIO(0x40000000,0x40000); print("PREVIOUS_CONTROL",m.read(0),"PREVIOUS_STATUS",m.read(4))' > before.log
python3 run_pynq.py --bitstream aster_linux.bit --firmware runtime.hex \
  --kind runtime --hart-count 2 --revision 71e257073960c5dee6d35af49efbeec9e1364687 \
  --output runtime.json > board_runtime.log 2>&1
echo 'PASS: board runtime'
for name in default_1 default_2 odd_1 odd_2 long_1 long_2 large_1 large_2; do
  python3 run_pynq.py --bitstream aster_linux.bit --firmware "$name.hex" \
    --kind parallel --hart-count 2 --revision 71e257073960c5dee6d35af49efbeec9e1364687 \
    --provenance "reference_$name.json" --output "$name.json" --no-download \
    > "board_$name.log" 2>&1
  echo "PASS: board $name"
done
python3 -c 'from pynq import MMIO; m=MMIO(0x40000000,0x40000); print("FINAL_CONTROL",m.read(0),"FINAL_STATUS",m.read(4),"FINAL_HART_STATUS",m.read(0x28)); assert m.read(0)==0 and m.read(4)==0 and m.read(0x28)==0' > final_state.log
