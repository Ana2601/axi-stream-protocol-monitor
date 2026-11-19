#!/usr/bin/env bash
# Run the AXI-Stream protocol monitor tests from a space-free temp dir.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional venv ($HW_VENV, default /tmp/hw-portfolio-venv); otherwise any python with cocotb installed.
VENV_FREE="${HW_VENV:-/tmp/hw-portfolio-venv}"
if [ -f "$VENV_FREE/bin/activate" ]; then source "$VENV_FREE/bin/activate"; fi
command -v cocotb-config >/dev/null || { echo "error: cocotb not found - run: pip install -r $HERE/requirements.txt"; exit 1; }

TMP="$(mktemp -d /tmp/cocotb_axis_mon.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

cp "$HERE/rtl/axis_fifo.sv" "$TMP/"
cp -r "$HERE/tb" "$TMP/tb"
cp -r "$HERE/vip" "$TMP/vip"
cp "$HERE/tb/test_axis_protocol.py" "$TMP/test_axis_protocol.py"

cat > "$TMP/Makefile" << 'EOF'
SIM ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL = axis_fifo
MODULE = test_axis_protocol
VERILOG_SOURCES = $(CURDIR)/axis_fifo.sv
EXTRA_ARGS += --timing -Wno-fatal -Wno-DECLFILENAME
PYTHONPATH = $(CURDIR)

include $(shell cocotb-config --makefiles)/Makefile.sim
EOF

cd "$TMP"
# Verilator 5.050 segfaults (simulator crash) when cocotb monitor/reader
# coroutines stay live across tests in one batch, although every test passes
# in isolation. Gate on isolated runs so the verification result stays real.
FAILS=0
for T in $(grep -oE "async def test_[a-z_0-9]+" "$HERE/tb/test_axis_protocol.py" | sed 's/async def //'); do
  LOG="$TMP/${T}.log"
  if COCOTB_TESTCASE="$T" make sim > "$LOG" 2>&1 && \
     grep -Eq "TESTS=[0-9]+ *PASS=1 *FAIL=0 " "$LOG"; then
    grep -E "$T passed|PASS" "$LOG" | tail -1
  else
    echo "FAILED: $T" >&2
    tail -20 "$LOG" >&2
    FAILS=$((FAILS + 1))
  fi
done
[ "$FAILS" -eq 0 ] || exit 1
