"""Layer 3 fidelity: assert the Python SED oracle (sed_model.py) reproduces the real
ARTIQ gateware (LaneDistributor) outcome-for-outcome and lane-for-lane.

Skipped automatically when migen + ARTIQ source are not importable (see l3_gateware.available).
Set up the environment with the project venv (migen + ARTIQ source cloned to .artiq-src).
"""

import random

import pytest

import l3_gateware
from sed_model import Event, Outcome, SEDModel

pytestmark = pytest.mark.skipif(
    not l3_gateware.available(),
    reason="migen + ARTIQ gateware not available",
)

_STATUS = {
    Outcome.OK: "ok",
    Outcome.SEQUENCE_ERROR: "sequence_error",
    Outcome.UNDERFLOW: "underflow",
}


def model_run(trace, lane_count=8):
    """Run SEDModel over (channel, coarse_ts) trace, normalized to the gateware's contract:
    minimum_coarse_timestamp=0 (gateware default), no draining, collision detection off."""
    events = [Event(channel=ch, coarse_ts=ts, replace=True) for ch, ts in trace]
    res = SEDModel(
        lanes=lane_count,
        lane_depth=1 << 30,          # effectively unbounded -> never a watermark switch
        enable_spread=False,
        minimum_coarse_timestamp=0,
    ).run(events)
    return [(_STATUS[s.outcome], s.lane) for s in res.steps]


# Curated traces that exercise each outcome and lane-distribution path.
CASES = {
    "fixed_increasing":   [(c, 1000 + c) for c in range(9)],
    "same_timestamp_9":   [(c, 1000) for c in range(9)],          # seq error on the 9th
    "exactly_8_lanes":    [(c, 1000) for c in range(8)],
    "descending":         [(c, 2000 - c) for c in range(9)],       # forced switch each event
    "lane_switch_steps":  [(40 + n, 8 + n) for n in range(32)],    # mirrors ARTIQ test_lane_switch
    "underflow_then_ok":  [(0, 5), (1, 0), (2, 10)],               # middle event underflows
    "interleaved":        [(0, 100), (1, 100), (2, 101), (3, 100), (4, 99)],
}


@pytest.mark.parametrize("name", list(CASES))
def test_model_matches_gateware_curated(name):
    trace = CASES[name]
    assert model_run(trace) == l3_gateware.gateware_run(trace), f"divergence on {name}"


@pytest.mark.parametrize("lane_count", [2, 4, 8])
def test_model_matches_gateware_lane_counts(lane_count):
    trace = [(c, 1000) for c in range(lane_count + 1)]   # one past capacity -> seq error
    assert model_run(trace, lane_count) == l3_gateware.gateware_run(trace, lane_count)


@pytest.mark.parametrize("seed", range(12))
def test_model_matches_gateware_fuzz(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 40)
    # Small timestamp window so equal/descending timestamps (the error-prone cases) are common.
    trace = [(rng.randint(0, 20), rng.randint(0, 6)) for _ in range(n)]
    assert model_run(trace) == l3_gateware.gateware_run(trace), f"divergence on seed {seed}: {trace}"
