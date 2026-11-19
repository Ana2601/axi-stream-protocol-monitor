// AXI4-Stream FIFO DUT with registered output and TKEEP support.
// Used as the target block for the AXI-Stream protocol monitor (VIP).
module axis_fifo #(
  parameter int DATA_WIDTH = 32,
  parameter int KEEP_WIDTH = 4,
  parameter int DEPTH      = 4
)(
  input  logic clk,
  input  logic rst_n,
  // AXI-Stream slave (input)
  input  logic               s_axis_tvalid,
  output logic               s_axis_tready,
  input  logic [DATA_WIDTH-1:0] s_axis_tdata,
  input  logic [KEEP_WIDTH-1:0] s_axis_tkeep,
  input  logic               s_axis_tlast,
  // AXI-Stream master (output)
  output logic               m_axis_tvalid,
  input  logic               m_axis_tready,
  output logic [DATA_WIDTH-1:0] m_axis_tdata,
  output logic [KEEP_WIDTH-1:0] m_axis_tkeep,
  output logic               m_axis_tlast,
  // Status
  output logic               fifo_full,
  output logic               fifo_empty,
  output logic [$clog2(DEPTH):0] fifo_count
);

  localparam int PTR_W = $clog2(DEPTH) + 1;
  logic [PTR_W-1:0] wr_ptr, rd_ptr;
  logic [DATA_WIDTH-1:0] mem [DEPTH];
  logic [KEEP_WIDTH-1:0] mem_keep [DEPTH];
  logic mem_last [DEPTH];

  logic [DATA_WIDTH-1:0] dout_q;
  logic [KEEP_WIDTH-1:0] dkeep_q;
  logic tlast_q;
  logic valid_q;

  logic [PTR_W-2:0] next_rd_idx;

  assign fifo_full  = (wr_ptr == {~rd_ptr[PTR_W-1], rd_ptr[PTR_W-2:0]});
  assign fifo_empty = (wr_ptr == rd_ptr);
  assign fifo_count = wr_ptr - rd_ptr;
  assign s_axis_tready = ~fifo_full;
  assign m_axis_tvalid = valid_q;
  assign m_axis_tdata  = dout_q;
  assign m_axis_tkeep  = dkeep_q;
  assign m_axis_tlast  = tlast_q;
  assign next_rd_idx  = rd_ptr[PTR_W-2:0] + 1'b1;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= '0;
    end else if (s_axis_tvalid && s_axis_tready) begin
      mem[wr_ptr[PTR_W-2:0]]      <= s_axis_tdata;
      mem_keep[wr_ptr[PTR_W-2:0]] <= s_axis_tkeep;
      mem_last[wr_ptr[PTR_W-2:0]] <= s_axis_tlast;
      wr_ptr <= wr_ptr + 1'b1;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_ptr  <= '0;
      dout_q  <= '0;
      dkeep_q <= '0;
      tlast_q <= 1'b0;
      valid_q <= 1'b0;
    end else begin
      if (valid_q && m_axis_tready) begin
        rd_ptr <= rd_ptr + 1'b1;
        if (wr_ptr != rd_ptr + 1'b1) begin
          valid_q <= 1'b1;
          dout_q  <= mem[next_rd_idx];
          dkeep_q <= mem_keep[next_rd_idx];
          tlast_q <= mem_last[next_rd_idx];
        end else begin
          valid_q <= 1'b0;
        end
      end else if (~valid_q && ~fifo_empty) begin
        valid_q <= 1'b1;
        dout_q  <= mem[rd_ptr[PTR_W-2:0]];
        dkeep_q <= mem_keep[rd_ptr[PTR_W-2:0]];
        tlast_q <= mem_last[rd_ptr[PTR_W-2:0]];
      end
    end
  end

endmodule
