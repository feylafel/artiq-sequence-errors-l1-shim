"""Tests for the example kernels in examples/, driven through the Layer 1 shim.

Run with `pytest` from the oracle/ directory.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from L1_shim import ARTIQShim
from sed_model import Event, Outcome, SEDModel
from examples.kernels import parallel_burst, sequence_error, simple_pulse
from examples.sequence_error_kernel import COARSE_RATIO, EVENT_TRACE, FIXED_TRACE, N

ORACLE_DIR = Path(__file__).resolve().parent.parent


def trace(shim):
    """The (channel, coarse_ts) stream, in submission order."""
    return [(e.channel, e.coarse_ts) for e in shim.get_events()]


def run_kernel(kernel, base_coarse_ts=0):
    """Run a kernel against a fresh shim, starting the cursor at the given coarse timestamp."""
    shim = ARTIQShim()
    if base_coarse_ts:
        shim.at_mu(base_coarse_ts * COARSE_RATIO)
    kernel(shim)
    return shim


def errors_of(result):
    return [(s.index, s.outcome) for s in result.errors]


def verdict_of_trace(pairs):
    """Run a hand-written [(channel, coarse_ts)] trace through the oracle."""
    return SEDModel(lanes=8).run([Event(channel=ch, coarse_ts=ts) for ch, ts in pairs])


# --- examples/kernels.py through the shim -------------------------------------------------


def test_simple_pulse():
    shim = run_kernel(simple_pulse)
    # delay_mu(100) lands 12 coarse cycles later: 100 >> 3 == 12
    assert trace(shim) == [(0, 0), (0, 12)]
    assert shim.verify(lanes=8).ok


def test_parallel_burst_submits_four_events_at_one_timestamp():
    shim = run_kernel(parallel_burst)
    assert trace(shim) == [(i, 0) for i in range(4)]
    res = shim.verify(lanes=8)
    assert res.ok
    assert [s.lane for s in res.steps] == [0, 1, 2, 3]   # equal ts forces a lane each time


def test_sequence_error_kernel_fails_on_the_ninth_event():
    shim = run_kernel(sequence_error)
    assert trace(shim) == [(i, 0) for i in range(9)]
    res = shim.verify(lanes=8)
    assert not res.ok
    assert errors_of(res) == [(8, Outcome.SEQUENCE_ERROR)]


# --- the shim reproduces the hand-written traces ---

def sequence_error_fixed(shim):
    """Mirror of SequenceErrorDemo.run_fixed: one coarse cycle between events."""
    for i in range(N):
        shim.ttl_on(i)
        shim.delay_mu(COARSE_RATIO)


def test_bad_kernel_reproduces_EVENT_TRACE():
    # The hand-written traces are stamped at coarse 1000, so start the cursor there.
    shim = run_kernel(sequence_error, base_coarse_ts=1000)
    assert trace(shim) == EVENT_TRACE


def test_fixed_kernel_reproduces_FIXED_TRACE():
    shim = run_kernel(sequence_error_fixed, base_coarse_ts=1000)
    assert trace(shim) == FIXED_TRACE


@pytest.mark.parametrize("kernel, hand_written", [
    (sequence_error, EVENT_TRACE),
    (sequence_error_fixed, FIXED_TRACE),
])
def test_shim_verdict_matches_hand_written_trace(kernel, hand_written):
    """The whole point of Layer 1: same verdict from a captured trace as from a written one."""
    shim = run_kernel(kernel, base_coarse_ts=1000)
    assert errors_of(shim.verify(lanes=8)) == errors_of(verdict_of_trace(hand_written))


# --- examples/run.py stays runnable --------------------------------------------------------


def test_run_py_executes_from_the_oracle_directory():
    """Guards the import convention: run.py broke while L1_shim imported `oracle.sed_model`."""
    proc = subprocess.run([sys.executable, "examples/run.py"], cwd=ORACLE_DIR,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "Sequence Error:" in proc.stdout
