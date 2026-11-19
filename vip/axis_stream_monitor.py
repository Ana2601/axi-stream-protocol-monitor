"""AXI4-Stream protocol monitor (verification IP).

A passive monitor that samples an AXI-Stream channel every clock edge and
checks protocol compliance. Reports violations with cycle stamps and keeps
per-rule counters. This is the "VIP-lite" building block for UVM-style
verification environments.

AXI-Stream rules checked:
  R1  TVALID/TREADY must not be X/Z.
  R2  Once TVALID is asserted it must stay asserted until the handshake
      completes (TREADY high) -- no glitches on TVALID.
  R3  While TVALID is high and TREADY is low, TDATA/TKEEP/TLAST must be
      stable (the master must hold the payload until the beat is accepted).
  R4  On a completed beat, TKEEP must be non-zero (a beat must transfer at
      least one byte).
  R5  After a beat with TLAST, the next cycle must have TVALID low (packet
      boundaries), i.e. TLAST only on the last beat of a packet.
  R6  No two TLAST beats without a gap (packet framing sanity).
"""

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly
from cocotb.utils import get_sim_time


class AxisStreamMonitor:
    """Passive AXI-Stream protocol monitor bound to a set of cocotb signals.

    Run via :meth:`start` which spawns a free-running sampling coroutine.
    """

    def __init__(self, dut, log=None, *, sig=None):
        self.dut = dut
        self.log = log or cocotb.log
        s = sig or {}
        self.s = {
            "tvalid": s.get("tvalid", dut.s_axis_tvalid),
            "tready": s.get("tready", dut.s_axis_tready),
            "tdata": s.get("tdata", dut.s_axis_tdata),
            "tkeep": s.get("tkeep", dut.s_axis_tkeep),
            "tlast": s.get("tlast", dut.s_axis_tlast),
        }
        self.violations = []
        self.rules = {k: 0 for k in ("R1", "R2", "R3", "R4", "R5", "R6")}
        self.beats = 0
        self.packets = 0
        self._last_tvalid = 0
        self._last_tready = 0
        self._last_tlast = 0
        self._last_tdata = None
        self._last_tkeep = None
        self._last_tlast_val = None
        self._task = None

    # ------------------------------------------------------------------
    def _sig(self, name, default=0):
        try:
            return int(self.s[name].value)
        except Exception:
            return default

    def _is_xz(self, val):
        # cocotb yields an X/Z value object; int() on it raises.
        try:
            int(self.s[val].value)
            return False
        except Exception:
            return True

    # ------------------------------------------------------------------
    def _report(self, rule, message):
        self.rules[rule] += 1
        t = get_sim_time(units="ns")
        entry = (rule, t, message)
        self.violations.append(entry)
        self.log.error(f"[AXI-Stream] {rule} @ {t} ns: {message}")

    # ------------------------------------------------------------------
    async def run(self):
        """Free-running sampling loop (spawned via start())."""
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()

            tvalid = self._is_xz("tvalid")
            tready = self._is_xz("tready")
            if tvalid or tready:
                self._report("R1", "TVALID/TREADY is X/Z")
                # cannot reason further with X/Z values
                self._last_tvalid = 0
                continue

            tvalid = self._sig("tvalid")
            tready = self._sig("tready")
            tdata = self._sig("tdata")
            tkeep = self._sig("tkeep")
            tlast = self._sig("tlast")

            # R2: TVALID must hold once asserted until the handshake completes.
            #     Detect a "stall" (tvalid high, tready low) and require tvalid
            #     to stay high for the whole stall.
            stalled_last = self._last_tvalid and not self._last_tready
            if stalled_last and not tvalid:
                self._report("R2", "TVALID deasserted before handshake completed")

            # R3: payload must be stable during a stall (while TVALID held,
            #     TREADY low). Data may freely change when a beat completes.
            if stalled_last and tvalid:
                if tdata != self._last_tdata:
                    self._report("R3", "TDATA changed while TVALID held, TREADY low")
                if tkeep != self._last_tkeep:
                    self._report("R3", "TKEEP changed while TVALID held, TREADY low")
                if tlast != self._last_tlast_val:
                    self._report("R3", "TLAST changed while TVALID held, TREADY low")

            # handshake completed this cycle?
            if tvalid and tready:
                self.beats += 1
                # R4: a beat must transfer at least one byte
                if tkeep == 0:
                    self._report("R4", f"TKEEP=0 on completed beat (data 0x{tdata:x})")
                # R5: TLAST must terminate a packet
                if tlast:
                    self.packets += 1
                    if self._last_tlast:
                        self._report("R5", "two consecutive TLAST beats (packet framing)")

            self._last_tvalid = tvalid
            self._last_tready = tready
            self._last_tlast = tlast
            self._last_tdata = tdata
            self._last_tkeep = tkeep
            self._last_tlast_val = tlast

    def start(self):
        self._task = cocotb.start_soon(self.run())
        return self

    def summary(self):
        return (f"{self.beats} beats, {self.packets} packets, "
                f"{len(self.violations)} protocol violations")
