"""
fs_ex3.py
-----------------------------------
Full-system x86 simulation that boots Linux under KVM for fast startup,
then switches to an O3 CPU for detailed performance modeling, using a
3-level Ruby/CHI cache hierarchy.

This script demonstrates:
    - KVM → O3 dynamic CPU switching via FactorySwitchableProcessor
    - 3-level Ruby/CHI cache hierarchy integration
    - Automated m5 exit-triggered phase transition during boot
    - Full-system Linux workload execution (resnet_mt example)

Dependencies:
    - gem5 compiled for X86 with KVM and Ruby support
    - binaries/vmlinux-4.4.186
    - disks/parsec.img (root partition 1)
"""

#!/usr/bin/env python3
import argparse

from gem5.utils.requires import requires
from gem5.isas import ISA
from gem5.components.boards.x86_board import X86Board
from gem5.components.memory.single_channel import SingleChannelDDR4_2400
from gem5.components.processors.cpu_types import CPUTypes
from gem5.resources.resource import KernelResource, DiskImageResource
from gem5.simulate.simulator import Simulator
from gem5.simulate.exit_event import ExitEvent

# custom modules
from hierarchy_ruby_3level import PrivateL1PrivateL2SharedL3CacheHierarchy
from custom_switch_processor import FactorySwitchableProcessor
from factories import kvm_core_factory_x86, o3_core_factory_x86


# ============================================================
# argparse
# ============================================================
parser = argparse.ArgumentParser()

# CPU pipeline widths
parser.add_argument("--Wf", type=int, default=12)
parser.add_argument("--Wd", type=int, default=10)
parser.add_argument("--Wr", type=int, default=10)
parser.add_argument("--Wi", type=int, default=12)
parser.add_argument("--Ww", type=int, default=10)
parser.add_argument("--Wc", type=int, default=12)

# CPU structure
parser.add_argument("--ROB", type=int, default=50)
parser.add_argument("--intRegs", type=int, default=50)
parser.add_argument("--fpRegs", type=int, default=50)
parser.add_argument("--LQ", type=int, default=128)
parser.add_argument("--SQ", type=int, default=128)

# Core count
parser.add_argument("--cores", type=int, default=4)

# Cache sizes
parser.add_argument("--L1", type=int, default=32)     # KB
parser.add_argument("--L2", type=int, default=256)    # KB
parser.add_argument("--L3", type=int, default=2048)   # KB (2MB default)
parser.add_argument("--L3slices", type=int, default=1)

args = parser.parse_args()

# ============================================================
# 將所有 argparse 參數寫入 outdir/config_used.txt 並計算 cost
# ============================================================
import m5

def write_config_to_file():

    outdir = m5.options.outdir
    filepath = f"{outdir}/config_used.txt"

    # ================================
    # CPU Cost
    # ================================
    Wf, Wd, Wr, Wi, Ww, Wc = args.Wf, args.Wd, args.Wr, args.Wi, args.Ww, args.Wc
    R = args.ROB
    I = args.intRegs
    F = args.fpRegs
    LQ = args.LQ
    SQ = args.SQ

    W = (Wf + Wd + Wr + Wi + Ww + Wc) / 6

    alpha, beta, gamma, delta, epsilon = 10, 12, 8, 6, 2
    kappa = 0.01
    CBP = 0  # tournament

    score = (
        alpha * W +
        beta * (R / 16) +
        gamma * (I / 32) +
        gamma * (F / 32) +
        delta * ((LQ + SQ) / 64) +
        epsilon * (Wi + Wc)
    )

    CLSQ = 0.05 * max(0, (LQ - 32) + (SQ - 32))

    per_core_cost = 40 + kappa * score + CBP + CLSQ
    CPU_cost = args.cores * per_core_cost

    # ================================
    # Cache Cost
    # ================================
    L1_cost = 3 * (args.L1 / 16) * args.cores * 2  # I + D
    L2_cost = (args.L2 / 16) * args.cores
    L3_cost = (args.L3 / 64) * args.L3slices

    # ================================
    # Memory Cost（固定 DDR4_2400）
    # ================================
    channels = 2
    capacity_gb = 3
    freq_step = (2400 - 1600) // 400
    mem_cost_ddr3 = (60 * channels) + (5 * freq_step * channels) + (10 * capacity_gb)
    mem_cost = 1.3 * mem_cost_ddr3  # DDR4 multiplier 1.3×

    # ================================
    # NoC Cost (Ring)
    # ================================
    n_routers = args.cores * 3 + 1 + args.L3slices
    noc_cost = 10 * n_routers + 5 * n_routers

    # ================================
    # Total Cost
    # ================================
    total_cost = CPU_cost + L1_cost + L2_cost + L3_cost + mem_cost + noc_cost
    total_cost_rounded = int(round(total_cost))

    # ================================
    # 寫入檔案
    # ================================
    with open(filepath, "w") as f:
        f.write("==== Parameter Configuration Used ====\n")
        for key, value in vars(args).items():
            f.write(f"{key} = {value}\n")

        f.write("\n==== Cost Breakdown ====\n")
        f.write(f"CPU_cost = {CPU_cost:.2f}\n")
        f.write(f"L1_cost = {L1_cost:.2f}\n")
        f.write(f"L2_cost = {L2_cost:.2f}\n")
        f.write(f"L3_cost = {L3_cost:.2f}\n")
        f.write(f"Memory_cost = {mem_cost:.2f}\n")
        f.write(f"NoC_cost = {noc_cost:.2f}\n")

        f.write("\n==== Total Cost ====\n")
        f.write(f"Total(before rounding) = {total_cost:.2f}\n")
        f.write(f"Total(rounded) = {total_cost_rounded}\n")

    print(f"[info] Parameters + cost written to {filepath}")


write_config_to_file()

# ============================================================
# 固定要求 (5.4)
# ============================================================
requires(
    isa_required=ISA.X86,
    kvm_required=True,
)


# ============================================================
# Processor setup: KVM → O3
# ============================================================
processor = FactorySwitchableProcessor(
    start_core_factory=kvm_core_factory_x86(ISA.X86),

    switch_core_factory=o3_core_factory_x86(
        ISA.X86,
        Wf=args.Wf,
        Wd=args.Wd,
        Wr=args.Wr,
        Wi=args.Wi,
        Ww=args.Ww,
        Wc=args.Wc,
        rob=args.ROB,
        n_int=args.intRegs,
        n_fp=args.fpRegs,
        lq=args.LQ,
        sq=args.SQ,
    ),

    num_cores=args.cores,
    isa=ISA.X86,
    starting_core_type_for_memmode=CPUTypes.KVM,
)

# disable host perf
for c in processor.get_cores():
    c.core.usePerf = False


# ============================================================
# Cache hierarchy (可調)
# ============================================================
cache_hierarchy = PrivateL1PrivateL2SharedL3CacheHierarchy(
    l1_size=f"{args.L1}KiB",
    l1_assoc=8,
    l2_size=f"{args.L2}KiB",
    l2_assoc=8,
    l3_size=f"{args.L3}KiB",
    l3_assoc=16,
    l3_slices=args.L3slices,
)

from gem5.components.memory import DualChannelDDR4_2400

# ============================================================
# Memory (你說先不要調 → 固定)
# ============================================================
memory = DualChannelDDR4_2400(size="3GB")


# ============================================================
# Board
# ============================================================
board = X86Board(
    clk_freq="3GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)


# ============================================================
# Workload
# ============================================================
kernel = KernelResource(local_path="binaries/vmlinux-4.4.186")
disk   = DiskImageResource(local_path="disks/parsec.img", root_partition="1")

rcs = f"""
echo "[rcS] Boot done → request CPU switch (KVM->O3)"
M5=$(command -v m5 || echo /sbin/m5)
$M5 exit

echo "[rcS] Now in O3, reset stats and run workload"
$M5 resetstats
cd /root
echo "[rcS] Running resnet_mt (threads={args.cores})"
./resnet_mt --channels 16 --size 8 --blocks 4 --threads {args.cores}
echo "[rcS] Done workload"
$M5 dumpstats
$M5 exit
"""

board.set_kernel_disk_workload(
    kernel=kernel,
    disk_image=disk,
    readfile_contents=rcs,
    kernel_args=["console=ttyS0", "earlyprintk=ttyS0", "root=/dev/hda1"],
)


# ============================================================
# Exit event handler
# ============================================================
def exit_event_handler():
    print("[handler] First exit: switching to O3")
    processor.switch()
    yield False

    print("[handler] Second exit: finishing simulation")
    yield True


# ============================================================
# Run
# ============================================================
simulator = Simulator(
    board=board,
    on_exit_event={ExitEvent.EXIT: exit_event_handler()},
)

simulator.run()
# ============================================================
# After Simulation: print simSeconds and Total Cost
# ============================================================
import os
import re

def extract_last_simseconds(stats_file):
    simsec = None
    pattern = re.compile(r"simSeconds\s+([0-9.]+)")

    with open(stats_file, "r") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                simsec = float(m.group(1))  # keep updating → last one remains
    return simsec

# locate stats.txt
outdir = m5.options.outdir
stats_path = os.path.join(outdir, "stats.txt")

# get simSeconds
simsec = extract_last_simseconds(stats_path)

# read cost from config_used.txt
config_path = os.path.join(outdir, "config_used.txt")
total_cost = None
with open(config_path, "r") as f:
    for line in f:
        if line.startswith("Total(rounded)"):
            total_cost = int(line.strip().split("=")[1])

print("\n===== Simulation Finished =====")
print(f"simSeconds = {simsec}")
print(f"TotalCost  = {total_cost}")
print("================================\n")
