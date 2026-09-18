# factories.py
# Core factory utilities for FactorySwitchableProcessor
# Provides modular factory functions for KVM and O3 cores (x86 ISA).
# These factories generate fully configured SimpleCore objects for gem5.
from gem5.isas import ISA
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_core import SimpleCore
from m5.objects import X86O3CPU
from m5.objects.BranchPredictor import TournamentBP

def kvm_core_factory_x86(isa: ISA):
    """
    Factory function for creating KVM-based SimpleCore instances (x86 ISA).

    Args:
        isa (ISA): The instruction set architecture used for the core.

    Returns:
        Callable[[int], SimpleCore]: A function that, when called with a core ID,
        returns a SimpleCore configured as a KVM core.
    """
    def _f(i: int) -> SimpleCore:
        # Create a SimpleCore with the KVM CPU type.
        return SimpleCore(cpu_type=CPUTypes.KVM, core_id=i, isa=isa)
    return _f

def o3_core_factory_x86(
    isa: ISA,
    *,
    Wf=10,
    Wd=10,
    Wr=10,
    Wi=10,
    Ww=10,
    Wc=10,
    rob=40,
    n_int=50,
    n_fp=50,
    lq=128,
    sq=128,
    bp="tournament",
):
    """
    Factory function for creating fully configured O3 cores for the x86 ISA.

    Args:
        isa (ISA): The instruction set architecture (ISA.X86).
        width (int): Pipeline width (fetch/issue/commit, etc.).
        rob (int): Number of entries in the Reorder Buffer (ROB).
        n_int (int): Number of physical integer registers.
        n_fp (int): Number of physical floating-point registers.
        lq (int): Load Queue size.
        sq (int): Store Queue size.
        bp (str): Branch predictor type ("tournament" supported by default).

    Returns:
        Callable[[int], SimpleCore]:
            A factory function that returns a SimpleCore wrapping an X86O3CPU
            with the above parameters applied.
    """

    def _f(i: int) -> SimpleCore:
        # Create a SimpleCore using the O3 CPU type.
        core = SimpleCore(cpu_type=CPUTypes.O3, core_id=i, isa=isa)
        
        # Access the underlying SimObject (X86O3CPU) to tune microarchitectural parameters.
        sim  = core.get_simobject()

        # Configure O3 pipeline widths.
        sim.fetchWidth  = Wf
        sim.decodeWidth = Wd
        sim.renameWidth = Wr
        sim.issueWidth  = Wi
        sim.wbWidth     = Ww
        sim.commitWidth = Wc

        # Configure structural parameters.
        sim.numROBEntries      = rob
        sim.numPhysIntRegs     = n_int
        sim.numPhysFloatRegs   = n_fp
        sim.LQEntries          = lq
        sim.SQEntries          = sq

        # Configure branch predictor.
        if bp == "tournament":
            sim.branchPred = TournamentBP()
        # Additional branch predictors can be added here if needed.

        return core
    return _f
