from typing import Callable, List, Optional, Dict

import m5

from gem5.isas import ISA
from gem5.components.boards.abstract_board import AbstractBoard
from gem5.components.boards.mem_mode import MemMode
from gem5.components.processors.abstract_core import AbstractCore
from gem5.components.processors.abstract_processor import AbstractProcessor
from gem5.components.processors.cpu_types import CPUTypes, get_mem_mode
from gem5.components.processors.simple_core import SimpleCore
from gem5.utils.override import overrides
from m5.util import warn


class FactorySwitchableProcessor(AbstractProcessor):
    """
    A flexible processor wrapper that supports runtime CPU switching
    (e.g., KVM → O3) using factory functions instead of fixed CPUTypes.

    - Each set of cores (start/switch) is created via user-defined factories.
    - Avoids dependency on CPUTypes.name (which causes attribute errors).
    - Allows direct customization of core parameters (e.g., O3 widths, ROB size).
    - Preserves KVM event queue setup and memory mode handling logic
      from gem5's standard SwitchableProcessor.
    """

    def __init__(
        self,
        start_core_factory: Callable[[int], AbstractCore],
        switch_core_factory: Callable[[int], AbstractCore],
        num_cores: int,
        isa: ISA,
        starting_core_type_for_memmode: CPUTypes = CPUTypes.KVM,
        mem_mode_override: Optional[MemMode] = None,
    ) -> None:
        assert num_cores > 0

        # Create two sets of cores: one for the starting phase and one for the switched phase.
        self._start_key = "start"
        self._switch_key = "switch"
        self._current_key = self._start_key

        self._cores: Dict[str, List[AbstractCore]] = {
            self._start_key: [start_core_factory(i) for i in range(num_cores)],
            self._switch_key: [switch_core_factory(i) for i in range(num_cores)],
        }

        # Ensure all cores share the same ISA.
        assert len({core.get_isa() for lst in self._cores.values() for core in lst}) == 1
        super().__init__(isa=isa)

        # Expose user-defined core groups as attributes (for clean stats printing).
        for k, lst in self._cores.items():
            setattr(self, k, lst)
            for core in lst:
                core.set_switched_out(core not in self._cores[self._current_key])

        # Prepare KVM VM object if any core uses KVM.
        self._prepare_kvm = any(core.is_kvm_core() for core in self._all_cores())
        if self._prepare_kvm:
            from m5.objects import KvmVM
            self.kvm_vm = KvmVM()

        # Determine the memory mode, optionally overridden by the user.
        self._mem_mode = mem_mode_override or get_mem_mode(
            starting_core_type_for_memmode
        )

    def _all_cores(self):
        """Iterate through all cores in both start and switch groups."""
        for lst in self._cores.values():
            for c in lst:
                yield c

    @overrides(AbstractProcessor)
    def incorporate_processor(self, board: AbstractBoard) -> None:
        """
        Incorporate this processor into the board.
        Also handles KVM event queue setup and Ruby/atomic memory mode adjustments.
        """
        # Store reference to the board for later use during CPU switching.
        self._board = board

        # Assign each KVM core to a unique event queue (consistent with stdlib).
        if self._prepare_kvm:
            kvm_cores = [c for c in self._all_cores() if c.is_kvm_core()]
            for i, core in enumerate(kvm_cores):
                for obj in core.get_simobject().descendants():
                    obj.eventq_index = 0
                core.get_simobject().eventq_index = i + 1

        # Warn if Ruby is used with an atomic core (same as SimpleSwitchableProcessor).
        if board.get_cache_hierarchy().is_ruby() and self._mem_mode == MemMode.ATOMIC:
            warn(
                "Using an atomic core with Ruby will result in 'atomic_noncaching' "
                "memory mode. This will skip caching completely."
            )
            self._mem_mode = MemMode.ATOMIC_NONCACHING

        board.set_mem_mode(self._mem_mode)

    @overrides(AbstractProcessor)
    def get_num_cores(self) -> int:
        """Return the number of currently active cores."""
        return len(self._cores[self._current_key])

    @overrides(AbstractProcessor)
    def get_cores(self) -> List[AbstractCore]:
        """Return the list of currently active cores."""
        return self._cores[self._current_key]

    def switch(self):
        """
        Toggle between the two core groups (start <-> switch).
        This is equivalent to calling `switch_to()` with the opposite key.
        """
        self.switch_to(self._switch_key if self._current_key == self._start_key
                       else self._start_key)

    def switch_to(self, key: str):
        """
        Perform the actual CPU switch operation using m5.switchCpus().
        Args:
            key (str): The target core set key ("start" or "switch").
        """
        assert hasattr(self, "_board"), "The processor has not been incorporated."
        assert key in self._cores, f"Unknown core set key: {key}"

        frm = self._cores[self._current_key]
        to  = self._cores[key]
        assert len(frm) == len(to), "Core counts must match when switching."

        frm_sim = [c.get_simobject() for c in frm]
        to_sim  = [c.get_simobject() for c in to]

        # Perform the low-level CPU swap.
        m5.switchCpus(self._board, list(zip(frm_sim, to_sim)))

        # Update switched_out flags for stats and consistency.
        for c in frm: c.set_switched_out(True)
        for c in to:  c.set_switched_out(False)

        self._current_key = key
