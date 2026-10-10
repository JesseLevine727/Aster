"""20.5: step 2's 8 KiB firmware (soc_l1_8k, f246a11) replayed on the new default build (soc_dev, 8 KiB by default):
the same console byte for byte and the same end, so the adopted default is the 8 KiB build step 2 measured."""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, "/home/elfo/Documents/Aster/scripts")
import matrix_golden
ROOT = Path("/home/elfo/Documents/Aster")
sim = ROOT / "build/aster_soc/soc_dev"
out = ROOT / "build/matrix/replay-8k"; out.mkdir(parents=True, exist_ok=True)
work = []
for run in (ROOT / "build/matrix/g2-geometry", ROOT / "build/matrix/g2-layouts"):
    work += [(run, e) for e in json.loads((run / "manifest.json").read_text())["entries"]
             if e["status"] == "captured" and e["sim"] == "soc_l1_8k"]
with ThreadPoolExecutor(8) as pool:
    differ = [d for d in pool.map(lambda w: matrix_golden.replay(sim, w[0], w[1], out, "riscv32-unknown-elf-"), work) if d]
print(f"{'PASS' if work and not differ else 'FAIL'}: {len(work) - len(differ)} of {len(work)} of step 2's 8 KiB firmware "
      f"images (soc_l1_8k, f246a11) give the same console and end on the new default build (soc_dev, 8 KiB)")
for d in differ[:20]:
    print("  " + d)
sys.exit(1 if differ or not work else 0)
