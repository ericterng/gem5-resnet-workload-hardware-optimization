# gem5 Project 5 — Cost-Constrained x86 Microarchitecture Design

A full-system gem5 study: design an x86 out-of-order processor with a 3-level
Ruby/CHI cache hierarchy, boot Linux on it, run a multithreaded ResNet
inference workload, and search the design space for the configuration that
minimizes execution time under a hardware cost model.

📄 **[Full report (PDF)](report.pdf)** · 📊 **[All 35 runs (CSV)](results/auto_results.csv)**

Course: 高等計算機結構 (Advanced Computer Architecture), 114-1

---

## What the simulation does

1. **Boot fast, measure accurately.** The machine boots Linux under KVM (native
   speed, no timing model). Once `rcS` reaches the end of boot it calls `m5 exit`,
   which triggers a switch to O3 detailed cores. Stats are reset *after* the
   switch, so boot time never pollutes the measurement.
2. **Run the workload.** `resnet_mt --channels 16 --size 8 --blocks 4 --threads N`
   — a multithreaded ResNet inference kernel, one thread per core.
3. **Report.** `simSeconds` from the region of interest is the score.

Every knob is exposed on the command line, and every run writes a
`config_used.txt` recording both the parameters and the resulting hardware cost.

## Repository layout

```
scripts/
  fs_ex3_edited.py              main config: board, CPU switching, workload, cost model
  fs_ex3_edited_dualmemory.py   identical, but DualChannelDDR4_2400 instead of single
  factories.py                  KVM and O3 core factories (widths, ROB, regs, LQ/SQ, BP)
  custom_switch_processor.py    FactorySwitchableProcessor — KVM → O3 handover
  hierarchy_ruby_3level.py      private L1 + private L2 + shared sliced L3 (Ruby/CHI)
  sort.py                       scrapes every run directory into auto_results.csv

results/
  auto_results.csv              aggregated table: parameters, cost, simSeconds
  run*/config_used.txt          exact parameters and cost breakdown per run

report.pdf                      written report
```

Raw gem5 output (`stats.txt`, `config.json`, `config.dot*`, …) is **not** tracked —
roughly 316 MB of reproducible artifacts. Re-run the scripts to regenerate it.

## Cost model

Each configuration is priced; the goal is performance *per unit cost*, not raw
performance. Implemented in `write_config_to_file()` in
[`scripts/fs_ex3_edited.py`](scripts/fs_ex3_edited.py).

| Component | Cost |
| --- | --- |
| CPU (per core) | `40 + 0.01·score + C_LSQ`, where `score` weights average pipeline width, ROB, physical registers and LQ/SQ |
| L1 | `3 · (L1/16) · cores · 2` (separate I and D) |
| L2 | `(L2/16) · cores` |
| L3 | `(L3/64) · slices` |
| Memory | DDR4-2400, 3 GB, single channel — `1.3 ×` the DDR3 baseline |
| NoC (ring) | `15 · (3·cores + 1 + slices)` routers |

## Tunable parameters

| Flag | Meaning | Default |
| --- | --- | --- |
| `--Wf --Wd --Wr --Wi --Ww --Wc` | fetch / decode / rename / issue / writeback / commit width | 12, 10, 10, 12, 10, 12 |
| `--ROB` | reorder buffer entries | 50 |
| `--intRegs --fpRegs` | physical register file sizes | 50, 50 |
| `--LQ --SQ` | load / store queue entries | 128, 128 |
| `--cores` | core count | 4 |
| `--L1 --L2 --L3` | cache sizes in KB | 32, 256, 2048 |
| `--L3slices` | shared L3 slices | 1 |

Branch predictor is a `TournamentBP` in all runs.

## Results

35 configurations were swept. Full table in
[`results/auto_results.csv`](results/auto_results.csv); the ends of the ranking:

| Run | Cost | simSeconds | Cores | L2 (KB) | L3 (KB) | ROB | Regs | Widths |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `run_opt4` | 1236 | 0.001474 | 4 | 2048 | 6144 | 192 | 96 | 12/12/12/12/12/12 |
| **`run18`** | **920** | **0.001476** | 4 | 1024 | 4096 | 128 | 96 | 12/12/12/12/12/12 |
| `run20` | 1268 | 0.001478 | 4 | 2048 | 8192 | 192 | 96 | 12/12/12/12/12/12 |
| **`run8`** | **794** | **0.001513** | 4 | 512 | 4096 | 192 | 96 | 12/12/12/12/12/12 |
| … | | | | | | | | |
| `run1` (baseline) | 692 | 0.001955 | 4 | 256 | 2048 | 50 | 50 | 12/10/10/12/10/12 |
| `run4` | 916 | 0.001992 | 4 | 1024 | 4096 | 50 | 50 | 12/10/10/12/10/12 |

### What the sweep showed

- **Performance saturates well before cost does.** `run18` matches the fastest
  configuration to within 0.2 % at **27 % lower cost** than `run_opt4` and
  **37 % lower** than `run20`. Beyond ~1 MB of L2 and 4 MB of L3 the workload
  stops benefiting.
- **The CPU core, not the cache, is the lever.** Every configuration in the top
  group runs uniform 12-wide pipelines with 96 physical registers. `run8` reaches
  0.001513 s on only 512 KB of L2 — cheaper than the 692-cost baseline's *cache*
  budget would suggest — because it spends on ROB and registers instead.
- **Narrow decode/rename is the bottleneck in the baseline.** Every run stuck at
  ≥ 0.0019 s has `Wd = Wr = 10` and 50 physical registers. Widening those two
  stages alone moves a configuration from the bottom of the table to the middle.
- **Extra cores did not pay.** `run20_6` (6 cores, cost 1758) is *slower* than
  the 4-core `run20` at cost 1268 — the workload does not scale past 4 threads
  at this problem size, and the wider ring adds latency.
- **Dual-channel memory did not pay either.** `run20_dual` costs 91 more than
  `run20` and is 4 % slower; this workload is not memory-bandwidth bound.
- **Slicing the L3 hurt.** `run_opt2` (2 slices, cost 1411) is the second-slowest
  run in the entire sweep — slice crossing cost more than the added bandwidth won.

**Best value: `run18`** — near-peak performance at 73 % of the fastest run's cost.

## Reproducing

Requires gem5 built for X86 with KVM and Ruby support, plus two resources that
are too large to track here:

```
binaries/vmlinux-4.4.186
disks/parsec.img          # root_partition="1", with resnet_mt in /root
```

Run one configuration:

```bash
gem5.opt -d results/run18 scripts/fs_ex3_edited.py \
  --Wf 12 --Wd 12 --Wr 12 --Wi 12 --Ww 12 --Wc 12 \
  --ROB 128 --intRegs 96 --fpRegs 96 --LQ 128 --SQ 128 \
  --cores 4 --L1 32 --L2 1024 --L3 4096 --L3slices 1
```

Then aggregate every run into a single table:

```bash
python scripts/sort.py
```

Note: `sort.py` has the scan directory hard-coded at the top (`root_dir`) — point
it at your output directory before running.
