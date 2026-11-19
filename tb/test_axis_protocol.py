"""Tests for the AXI-Stream protocol monitor (VIP).

Two kinds of tests:
  1. clean_handshake / random_sanity -- drive COMPLIANT AXI-Stream traffic
     through a real FIFO DUT using a proper master BFM (holds the bus until
     the handshake completes); the monitor must report ZERO violations.
  2. injected_violations -- create real backpressure (fill the FIFO), then
     drive deliberately non-compliant signalling; the monitor must catch each
     violation (proves the monitor works).

The monitor watches the slave (write) interface. R4/TKEEP is caught on both
the write and the read (pass-through) path, so it may be reported twice.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly, NextTimeStep
import random

from vip.axis_stream_monitor import AxisStreamMonitor

DATA_WIDTH = 32
DEPTH = 4
MASK = (1 << DATA_WIDTH) - 1


async def drive_cycle(dut, tvalid, tdata=0, tkeep=0xF, tlast=0):
    """Apply one cycle of inputs on the write side and advance one clock.

    Does NOT touch m_axis_tready: that signal is owned by the background
    reader (or left at 0 when no reader is running, e.g. backpressure tests).
    """
    dut.s_axis_tvalid.value = tvalid
    dut.s_axis_tdata.value = tdata & MASK
    dut.s_axis_tkeep.value = tkeep
    dut.s_axis_tlast.value = tlast
    await RisingEdge(dut.clk)
    await ReadOnly()
    await NextTimeStep()


async def master_beat(dut, tdata, tlast=0, max_wait=20):
    """Compliant master write: hold tvalid/data until accepted.

    tready is combinational (~fifo_full); we sample it *before* the edge that
    will consume the current tvalid, so a high reading means the beat is
    accepted at this edge.
    """
    dut.s_axis_tvalid.value = 1
    dut.s_axis_tdata.value = tdata & MASK
    dut.s_axis_tkeep.value = 0xF
    dut.s_axis_tlast.value = tlast
    for _ in range(max_wait):
        rdy = int(dut.s_axis_tready.value)
        await RisingEdge(dut.clk)
        await ReadOnly()
        if rdy:
            await NextTimeStep()
            dut.s_axis_tvalid.value = 0
            return True
    # give up cleanly (deassert before returning) to avoid a lingering stall
    await NextTimeStep()
    dut.s_axis_tvalid.value = 0
    return False


async def fill_fifo(dut):
    """Write DEPTH beats to fill the FIFO (creates backpressure)."""
    for i in range(DEPTH):
        await master_beat(dut, tdata=0xA000 | i, tlast=0)


async def reset(dut):
    dut.rst_n.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tkeep.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def drain_reader(dut):
    """Background reader: always ready, pops every entry as it arrives."""
    while True:
        dut.m_axis_tready.value = 1
        await RisingEdge(dut.clk)
        await NextTimeStep()


def start_reader(dut):
    cocotb.start_soon(drain_reader(dut))


@cocotb.test()
async def test_clean_handshake(dut):
    """Compliant master traffic -> monitor reports no violations."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    mon = AxisStreamMonitor(dut).start()
    mon.log = cocotb.log.getChild("monitor")
    start_reader(dut)  # read side always drains -> write never stalls

    await ClockCycles(dut.clk, 2)

    # compliant packet: 4 beats, last one with tlast
    for i in range(DEPTH):
        await master_beat(dut, tdata=i * 0x1111, tlast=(i == DEPTH - 1))
    # drain any residual entries on the read side
    for _ in range(DEPTH + 2):
        await drive_cycle(dut, 0)

    await ClockCycles(dut.clk, 4)
    await ReadOnly()

    cocotb.log.info(f"clean: monitor {mon.summary()}")
    assert mon.beats >= DEPTH, f"expected beats, got {mon.beats}"
    assert mon.violations == [], (
        f"clean traffic must have 0 violations, got {mon.violations}")


@cocotb.test()
async def test_random_sanity(dut):
    """Random but COMPLIANT traffic -> zero violations."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    mon = AxisStreamMonitor(dut).start()
    mon.log = cocotb.log.getChild("monitor")
    start_reader(dut)  # read side always drains

    random.seed(7)
    for _ in range(120):
        if random.random() < 0.7:
            await master_beat(dut, tdata=random.getrandbits(32),
                              tlast=(random.random() < 0.15))
        # always insert an idle cycle -> the FIFO can never fill, so the
        # master never stalls and every handshake completes in one cycle.
        await drive_cycle(dut, 0)

    await ClockCycles(dut.clk, 4)
    await ReadOnly()

    cocotb.log.info(f"random: monitor {mon.summary()}")
    assert mon.beats > 0, "expected some beats"
    assert mon.violations == [], (
        f"compliant random traffic must be clean, got {mon.violations}")


@cocotb.test()
async def test_violation_tvalid_glitch(dut):
    """R2: TVALID deasserted mid-handshake must be caught."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    mon = AxisStreamMonitor(dut).start()
    mon.log = cocotb.log.getChild("monitor")

    # fill the FIFO so tready is low (real backpressure)
    await fill_fifo(dut)
    assert int(dut.s_axis_tready.value) == 0, "FIFO must be full now"

    # hold tvalid high (handshake not done) ...
    await drive_cycle(dut, 1, tdata=0xDEADBEEF)
    # ... then glitch tvalid low while tready is still low
    await drive_cycle(dut, 0, tdata=0xDEADBEEF)
    await ClockCycles(dut.clk, 2)

    assert mon.rules["R2"] >= 1, "monitor must flag TVALID glitch (R2)"
    cocotb.log.info(f"violation test caught: {mon.violations}")


@cocotb.test()
async def test_violation_tkeep_zero(dut):
    """R4: TKEEP=0 on a completed beat must be caught."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    mon = AxisStreamMonitor(dut).start()
    mon.log = cocotb.log.getChild("monitor")

    # a completed beat (tready high) with tkeep=0
    await master_beat(dut, tdata=0xCAFEBABE)  # normal beat first (tready high)
    await drive_cycle(dut, 1, tdata=0xCAFEBABE, tkeep=0x0)
    await ClockCycles(dut.clk, 2)

    assert mon.rules["R4"] >= 1, "monitor must flag TKEEP=0 beat (R4)"
    cocotb.log.info(f"violation test caught: {mon.violations}")


@cocotb.test()
async def test_violation_stable_payload(dut):
    """R3: TDATA changing while TVALID held and TREADY low must be caught."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    mon = AxisStreamMonitor(dut).start()
    mon.log = cocotb.log.getChild("monitor")

    # fill the FIFO so tready is low
    await fill_fifo(dut)
    assert int(dut.s_axis_tready.value) == 0, "FIFO must be full now"

    # hold tvalid high with tready low, then change the data mid-hold
    await drive_cycle(dut, 1, tdata=0x11111111)
    await drive_cycle(dut, 1, tdata=0x22222222)  # data change mid-hold
    await ClockCycles(dut.clk, 2)

    assert mon.rules["R3"] >= 1, "monitor must flag unstable payload (R3)"
    cocotb.log.info(f"violation test caught: {mon.violations}")
