"""
===============================================================================
Three-Level Ruby/CHI Cache Hierarchy for gem5
===============================================================================

This file defines a 3-level cache hierarchy using the CHI protocol over
SimplePt2Pt topology. Each core has private L1I/L1D and private L2 caches,
and all cores share one or more L3 Home Nodes (HN). This model is suitable
for detailed timing simulations (TIMING mode) and is compatible with Ruby.

Usage Example
-------------
from hierarchy_l1l2l3 import PrivateL1PrivateL2SharedL3CacheHierarchy

cache_hierarchy = PrivateL1PrivateL2SharedL3CacheHierarchy(
    l1_size="32KiB", l1_assoc=8,
    l2_size="256KiB", l2_assoc=8,
    l3_size="2MiB",  l3_assoc=16,
    l3_slices=1,     # set >1 to enable simple address-based slicing
)

Notes
-----
- Adapted from the 2-level CHI Ruby example, extended to 3 levels.
- Sequencers are attached at L1 (as required by Ruby convention).
- Uses the SimplePt2Pt topology for simplicity.
- Designed to work in both SE and FS modes.

===============================================================================
"""

from itertools import chain
from typing import List

from gem5.components.cachehierarchies.chi.nodes.abstract_node import AbstractNode
from gem5.components.cachehierarchies.chi.nodes.memory_controller import MemoryController
from gem5.components.cachehierarchies.chi.nodes.private_l1_moesi_cache import PrivateL1MOESICache
from gem5.components.cachehierarchies.chi.nodes.dma_requestor import DMARequestor
from gem5.components.cachehierarchies.ruby.abstract_ruby_cache_hierarchy import AbstractRubyCacheHierarchy
from gem5.components.cachehierarchies.ruby.topologies.simple_pt2pt import SimplePt2Pt
from gem5.isas import ISA

from m5.objects import (
    TreePLRURP, RRIPRP,
    RubyNetwork, RubyCache, RubySystem, RubySequencer, RubyPortProxy,
    AddrRange, SubSystem, NULL
)

# -----------------------------------------------------------------------------
# Helper: split address ranges evenly for multiple L3 slices
# -----------------------------------------------------------------------------
def _split_ranges_evenly(ranges: List[AddrRange], parts: int) -> List[List[AddrRange]]:
    """
    Evenly divide address ranges into `parts` sub-lists.

    Parameters
    ----------
    ranges : List[AddrRange]
        The memory ranges available in the system.
    parts : int
        Number of L3 slices to split into.

    Returns
    -------
    List[List[AddrRange]]
        A list of lists of AddrRange objects for each slice.
    """
    if parts <= 1:
        return [list(ranges)]

    total = sum(int(r.size()) for r in ranges)
    target = total // parts

    out = [[] for _ in range(parts)]
    cur_slice = 0
    cur_accum = 0

    for r in ranges:
        start, end = int(r.start), int(r.start + r.size())
        ptr = start
        while ptr < end:
            rem = end - ptr
            need = target - cur_accum if cur_slice < parts - 1 else rem
            chunk = min(rem, max(need, 1))
            out[cur_slice].append(AddrRange(ptr, ptr + chunk - 1))
            ptr += chunk
            cur_accum += chunk
            if cur_slice < parts - 1 and cur_accum >= target:
                cur_slice += 1
                cur_accum = 0
    return out


# -----------------------------------------------------------------------------
# Shared L3 Home Node
# -----------------------------------------------------------------------------
class SharedL3(AbstractNode):
    """
    Shared L3 Home Node (HN) cache.
    This is the final caching level before memory controllers.

    It handles coherence requests for all address ranges it covers.
    """

    def __init__(self, size: str, assoc: int, network: RubyNetwork, cache_line_size: int):
        super().__init__(network, cache_line_size)

        self.cache = RubyCache(size=size, assoc=assoc, replacement_policy=RRIPRP())
        self.sequencer = NULL
        self.send_evictions = False
        self.use_prefetcher = False
        self.prefetcher = NULL

        # CHI-specific configuration
        self.is_HN = True
        self.enable_DMT = True
        self.enable_DCT = True
        self.allow_SD = True

        # Allocation / deallocation policy
        self.alloc_on_seq_acc = False
        self.alloc_on_seq_line_write = False
        self.alloc_on_readshared = True
        self.alloc_on_readunique = False
        self.alloc_on_readonce = True
        self.alloc_on_writeback = True
        self.alloc_on_atomic = True
        self.dealloc_on_unique = True
        self.dealloc_on_shared = False
        self.dealloc_backinv_unique = False
        self.dealloc_backinv_shared = False

        # Transient Buffer Entry (TBE) parameters
        self.number_of_TBEs = 64
        self.number_of_repl_TBEs = 64
        self.number_of_snoop_TBEs = 8
        self.number_of_DVM_TBEs = 2
        self.number_of_DVM_snoop_TBEs = 2
        self.unify_repl_TBEs = False


# -----------------------------------------------------------------------------
# Private L2 (Non-Home) Cache
# -----------------------------------------------------------------------------
class PrivateL2MOESICache(AbstractNode):
    """
    Per-core Private L2 Cache (non-Home Node).

    This layer acts as an intermediate cache between L1 and L3.
    It does not maintain home-node responsibilities and does not
    directly generate coherence callbacks to sequencers.
    """

    def __init__(self, size, assoc, network, cache_line_size, clk_domain=None):
        super().__init__(network, cache_line_size)

        self.cache = RubyCache(size=size, assoc=assoc, replacement_policy=TreePLRURP())
        self.sequencer = NULL  # L2 has no sequencer
        self.send_evictions = False
        self.use_prefetcher = False
        self.prefetcher = NULL
        self.is_HN = False

        if clk_domain is not None:
            self.clk_domain = clk_domain

        # Allocation / deallocation policy (aligned with L3)
        self.alloc_on_seq_acc = False
        self.alloc_on_seq_line_write = False
        self.alloc_on_readshared = True
        self.alloc_on_readunique = False
        self.alloc_on_readonce = True
        self.alloc_on_writeback = True
        self.alloc_on_atomic = True
        self.dealloc_on_unique = True
        self.dealloc_on_shared = False
        self.dealloc_backinv_unique = False
        self.dealloc_backinv_shared = False

        # TBE parameters
        self.number_of_TBEs = 64
        self.number_of_repl_TBEs = 64
        self.number_of_snoop_TBEs = 8
        self.number_of_DVM_TBEs = 2
        self.number_of_DVM_snoop_TBEs = 2
        self.unify_repl_TBEs = False

        # CHI-specific configuration
        self.allow_SD = True
        self.enable_DCT = True
        self.enable_DMT = True


# -----------------------------------------------------------------------------
# 3-Level Ruby/CHI Hierarchy
# -----------------------------------------------------------------------------
class PrivateL1PrivateL2SharedL3CacheHierarchy(AbstractRubyCacheHierarchy):
    """
    Full 3-Level Ruby/CHI Cache Hierarchy:
      L1I/L1D (private) → L2 (private) → L3 (shared Home Node)

    Designed for multi-core Ruby CHI simulations using SimplePt2Pt topology.
    """

    def __init__(self, l1_size, l1_assoc, l2_size, l2_assoc, l3_size, l3_assoc, l3_slices=1):
        super().__init__()
        self._l1_size   = l1_size
        self._l1_assoc  = l1_assoc
        self._l2_size   = l2_size
        self._l2_assoc  = l2_assoc
        self._l3_size   = l3_size
        self._l3_assoc  = l3_assoc
        self._l3_slices = max(1, int(l3_slices))

    # -------------------------------------------------------------------------
    # Build Ruby system and interconnect caches
    # -------------------------------------------------------------------------
    def incorporate_cache(self, board):
        """Integrate this hierarchy into a gem5 board."""

        # Create Ruby system and network
        self.ruby_system = RubySystem()
        self.ruby_system.network = SimplePt2Pt(self.ruby_system)
        self.ruby_system.number_of_virtual_networks = 4
        self.ruby_system.network.number_of_virtual_networks = 4

        # ---------------------------------------------------------------------
        # L3 Home Nodes
        # ---------------------------------------------------------------------
        l3_nodes = []
        for i in range(self._l3_slices):
            l3 = SharedL3(
                size=self._l3_size,
                assoc=self._l3_assoc,
                network=self.ruby_system.network,
                cache_line_size=board.get_cache_line_size(),
            )
            l3.ruby_system = self.ruby_system
            l3_nodes.append(l3)
            self.add_child(f"l3_{i}", l3)

        # Address-range partitioning (for multiple L3 slices)
        if self._l3_slices > 1:
            all_mem_ranges = [rng for rng, _ in board.get_mem_ports()]
            splitted = _split_ranges_evenly(all_mem_ranges, self._l3_slices)
            for idx, l3 in enumerate(l3_nodes):
                l3.addr_ranges = splitted[idx]

        # ---------------------------------------------------------------------
        # Per-Core Cluster (L1I/L1D + L2)
        # ---------------------------------------------------------------------
        core_clusters = []
        cores = board.get_processor().get_cores()
        ncores = len(cores)

        for cid, core in enumerate(cores):
            cluster = SubSystem()

            # --- L1 Caches (Data + Instruction)
            cluster.dcache = PrivateL1MOESICache(
                size=self._l1_size, assoc=self._l1_assoc,
                network=self.ruby_system.network, core=core,
                cache_line_size=board.get_cache_line_size(),
                target_isa=board.get_processor().get_isa(),
                clk_domain=board.get_clock_domain(),
            )
            cluster.icache = PrivateL1MOESICache(
                size=self._l1_size, assoc=self._l1_assoc,
                network=self.ruby_system.network, core=core,
                cache_line_size=board.get_cache_line_size(),
                target_isa=board.get_processor().get_isa(),
                clk_domain=board.get_clock_domain(),
            )

            # --- L2 Cache (Private)
            cluster.l2 = PrivateL2MOESICache(
                size=self._l2_size, assoc=self._l2_assoc,
                network=self.ruby_system.network,
                cache_line_size=board.get_cache_line_size(),
                clk_domain=board.get_clock_domain(),
            )

            # Attach Ruby system
            for c in (cluster.dcache, cluster.icache, cluster.l2):
                c.ruby_system = self.ruby_system

            # --- Sequencers on L1 (Ruby requirement)
            cluster.icache.sequencer = RubySequencer(
                version=2*cid + 1,
                dcache=cluster.icache.cache,
                clk_domain=cluster.icache.clk_domain,
            )
            cluster.icache.sequencer.ruby_system = self.ruby_system

            cluster.dcache.sequencer = RubySequencer(
                version=2*cid,
                dcache=cluster.dcache.cache,
                clk_domain=cluster.dcache.clk_domain,
            )
            cluster.dcache.sequencer.ruby_system = self.ruby_system

            # --- Connect CPU ports
            core.connect_icache(cluster.icache.sequencer.in_ports)
            core.connect_dcache(cluster.dcache.sequencer.in_ports)
            core.connect_walker_ports(
                cluster.dcache.sequencer.in_ports, cluster.icache.sequencer.in_ports
            )
            if board.get_processor().get_isa() == ISA.X86:
                core.connect_interrupt(
                    cluster.dcache.sequencer.interrupt_out_port,
                    cluster.dcache.sequencer.in_ports,
                )
            else:
                core.connect_interrupt()

            # --- Downstream connections
            cluster.icache.downstream_destinations = [cluster.l2]
            cluster.dcache.downstream_destinations = [cluster.l2]
            cluster.l2.downstream_destinations     = l3_nodes

            # Optional I/O connection for DMA/MMIO
            if board.has_io_bus():
                cluster.dcache.sequencer.connectIOPorts(board.get_io_bus())

            self.add_child(f"cluster{cid}", cluster)
            core_clusters.append(cluster)

        # ---------------------------------------------------------------------
        # Memory Controllers
        # ---------------------------------------------------------------------
        memory_controllers = []
        for i, (rng, port) in enumerate(board.get_mem_ports()):
            mc = MemoryController(self.ruby_system.network, rng, port)
            mc.ruby_system = self.ruby_system
            memory_controllers.append(mc)
            self.add_child(f"mc{i}", mc)

        for l3 in l3_nodes:
            l3.downstream_destinations = memory_controllers

        # ---------------------------------------------------------------------
        # DMA Controllers
        # ---------------------------------------------------------------------
        dma_controllers = []
        if board.has_dma_ports():
            for i, port in enumerate(board.get_dma_ports()):
                ctrl = DMARequestor(
                    self.ruby_system.network,
                    board.get_cache_line_size(),
                    board.get_clock_domain(),
                )
                version = ncores + i
                ctrl.sequencer = RubySequencer(version=version, in_ports=port, dcache=NULL)
                ctrl.ruby_system = self.ruby_system
                ctrl.sequencer.ruby_system = self.ruby_system
                ctrl.downstream_destinations = l3_nodes

                dma_controllers.append(ctrl)
                self.add_child(f"dma{i}", ctrl)

            self.ruby_system.num_of_sequencers = ncores * 2 + len(dma_controllers)
        else:
            self.ruby_system.num_of_sequencers = ncores * 2

        # ---------------------------------------------------------------------
        # Connect all controllers to the Ruby network
        # ---------------------------------------------------------------------
        controllers_in_order = list(
            chain.from_iterable([(c.dcache, c.icache, c.l2) for c in core_clusters])
        )
        controllers_in_order += l3_nodes
        controllers_in_order += memory_controllers
        controllers_in_order += dma_controllers

        self.ruby_system.network.connectControllers(controllers_in_order)
        self.ruby_system.network.setup_buffers()

        # ---------------------------------------------------------------------
        # System port proxy for top-level memory access
        # ---------------------------------------------------------------------
        self.ruby_system.sys_port_proxy = RubyPortProxy()
        self.ruby_system.sys_port_proxy.ruby_system = self.ruby_system

        board.connect_system_port(self.ruby_system.sys_port_proxy.in_ports)
