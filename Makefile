SIM ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL = axis_fifo
MODULE = test_axis_protocol
VERILOG_SOURCES = $(CURDIR)/rtl/axis_fifo.sv
EXTRA_ARGS += --timing -Wno-fatal -Wno-DECLFILENAME
PYTHONPATH = $(CURDIR)

include $(shell cocotb-config --makefiles)/Makefile.sim

sim:
	./run_sim.sh

.PHONY: sim
