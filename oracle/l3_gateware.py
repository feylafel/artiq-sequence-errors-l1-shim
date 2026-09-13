"""Layer 3: drive ARTIQ's real RTIO lane distributor and report per-event outcomes.

This wraps the actual gateware (`artiq.gateware.rtio.sed.lane_distributor.LaneDistributor`)
in a migen `run_simulation` testbench and returns, for each submitted event, whether it was
written OK / sequence-errored / underflowed, and which lane it landed in. That is the ground
truth our Layer 2 Python oracle (`sed_model.py`) must reproduce.

The testbench is adapted from ARTIQ's own `test_sed_lane_distributor.py::simulate`, run with
all lanes permanently writable (`wait=False` equivalent) so that no draining/back-pressure or
spread occurs — matching the conservative no-drain assumption in SEDModel.

Requires `migen` + the ARTIQ source on the path. If unavailable, `available()` returns False
and the pytest fidelity tests skip.

Unit convention: SEDModel works in *coarse* timestamps; the gateware takes *fine* timestamps
and derives coarse = fine >> FINE_TS_WIDTH. We map model coarse_ts -> fine = coarse << 3, so
one model coarse unit == one coarse RTIO cycle.
"""

from __future__ import annotations

import os
import sys

FINE_TS_WIDTH = 3            # coarse = fine >> 3  (8 fine ticks per coarse cycle)
_DEFAULT_ARTIQ_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".artiq-src")


def _ensure_artiq_on_path() -> None:
    src = os.environ.get("ARTIQ_SRC", _DEFAULT_ARTIQ_SRC)
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def available() -> bool:
    """True if migen + ARTIQ's LaneDistributor can be imported in this environment."""
    _ensure_artiq_on_path()
    try:
        import migen  # noqa: F401
        from artiq.gateware.rtio.sed.lane_distributor import LaneDistributor  # noqa: F401
    except Exception:
        return False
    return True


def gateware_run(trace, lane_count: int = 8):
    """Run the real LaneDistributor over `trace` (list of (channel, coarse_ts)).

    Returns a list of (status, lane) per submitted event, in submission order, where status is
    one of "ok" / "sequence_error" / "underflow" and lane is the int lane for "ok" events
    (None otherwise). Channels must be < 256 (8-bit channel field).
    """
    _ensure_artiq_on_path()
    from migen import run_simulation, passive
    from artiq.gateware.rtio import cri
    from artiq.gateware.rtio.sed import lane_distributor

    layout = [("channel", 8), ("timestamp", 32)]
    compensation = [0] * 256
    dut = lane_distributor.LaneDistributor(lane_count, 8, layout, compensation, FINE_TS_WIDTH)

    write_lanes = []          # (seqn, lane) for each successful write, in sim order
    access_results = []       # status per submitted event, in submission order

    def gen():
        for channel, coarse_ts in trace:
            fine_ts = coarse_ts << FINE_TS_WIDTH
            yield dut.cri.chan_sel.eq(channel)
            yield dut.cri.o_timestamp.eq(fine_ts)
            yield
            yield dut.cri.cmd.eq(cri.commands["write"])
            yield
            yield dut.cri.cmd.eq(cri.commands["nop"])
            yield
            while (yield dut.cri.o_status) & 0x01:   # wait while busy
                yield
            status = (yield dut.cri.o_status)
            if status & 0x02:
                access_results.append("underflow")
            elif (yield dut.sequence_error):
                access_results.append("sequence_error")
            else:
                access_results.append("ok")

    @passive
    def monitor_lane(n, lio):
        yield lio.writable.eq(1)                      # lane always writable -> no drain/spread
        while True:
            while not (yield lio.we):
                yield
            seqn = (yield lio.seqn)
            write_lanes.append((seqn, n))
            yield

    generators = [gen()]
    for n, lio in enumerate(dut.output):
        lio.writable.reset = 1
        generators.append(monitor_lane(n, lio))
    run_simulation(dut, generators)

    # Map successful writes (ordered by seqn) onto the "ok" events in submission order.
    lanes_by_write = [lane for _, lane in sorted(write_lanes)]
    out = []
    w = 0
    for status in access_results:
        if status == "ok":
            out.append((status, lanes_by_write[w]))
            w += 1
        else:
            out.append((status, None))
    return out


if __name__ == "__main__":
    if not available():
        print("gateware unavailable (need migen + ARTIQ source)")
        sys.exit(2)
    bad = [(c, 1000) for c in range(9)]
    for i, (status, lane) in enumerate(gateware_run(bad)):
        print(f"  [{i}] {status:<15} lane={lane}")
