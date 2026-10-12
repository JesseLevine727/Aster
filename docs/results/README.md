# Evidence

Each phase's evidence bundle, with its own README and `SHA256SUMS`:

| Folder | What it holds |
| --- | --- |
| `phase17/` | The retained baseline measurements the targets were set against |
| `phase18/` | The Aster core: lockstep, conformance, kernels, timing, the board |
| `phase19/` | The NPU: the shell, the options studied, the SoC, the board |
| `phase20/` | The two-core SoC: each milestone, the final workload matrix (`matrix-20.5`), the board run (`board-20.5`), the report's tables (`report-20.5`) |
| `phase21/` | The 100 MHz closure (`closure-21`) |

Three small folders hold frozen inputs that the workloads and reference models read:

| Folder | Contents |
| --- | --- |
| `phase11/` | The MNIST MLP: the trained model and its export (`model.json`, `export.json`, `reference.json`) |
| `workloads/` | The CIFAR-10 CNN: the trained model and its export (`cifar_model.json`, `cifar_export.json`) |
| `phase12/` | The MIT-BIH ECG segment the streaming workload processes (`ecg_segment.json`) |

They are frozen. The matrix's oracles and the reference models read the two models and the ECG segment.
`scripts/gen_mnist_transposed.py` and `scripts/gen_cifar_hwc.py` build the NPU methods' reordered weights, and CIFAR's
images, from the models.
`scripts/gen_ecg_data.py` wrote the ECG segment, beside its C header, from PhysioNet's MIT-BIH record 100.
