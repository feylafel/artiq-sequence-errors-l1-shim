"""Tests for the Layer 1 kernel-API shim (L1_shim.py).

Run with `pytest` from the oracle/ directory.
"""

import pytest

from L1_shim import ARTIQShim
from sed_model import Outcome, SEDModel


def trace(shim):
    """The (channel, coarse_ts) stream, in submission order."""
    return [(e.channel, e.coarse_ts) for e in shim.get_events()]


def outcomes(result):
    return [s.outcome for s in result.steps]


def lanes(result):
    return [s.lane for s in result.steps]


def verdict(shim):
    return SEDModel(lanes=8).run(shim.get_events())


def test_exercise1_straight_line_spread_out():
    shim = ARTIQShim()
    for i in range(4):
        shim.ttl_on(i)
        shim.delay_mu(8)

    assert trace(shim) == [(0, 0), (1, 1), (2, 2), (3, 3)]
    res = verdict(shim)
    assert res.ok
    assert lanes(res) == [0, 0, 0, 0]       # each strictly later, all stack in lane 0


def test_exercise2_eight_at_once_exactly_fills_the_lanes():
    shim = ARTIQShim()
    for i in range(8):
        shim.ttl_on(i)

    assert trace(shim) == [(i, 0) for i in range(8)]
    res = verdict(shim)
    assert res.ok
    assert lanes(res) == list(range(8))


def test_exercise3_nine_at_once_is_a_sequence_error():
    shim = ARTIQShim()
    for i in range(9):
        shim.ttl_on(i)

    assert trace(shim) == [(i, 0) for i in range(9)]
    res = verdict(shim)
    assert not res.ok
    assert len(res.errors) == 1
    assert res.errors[0].index == 8         # the 9th event wraps to lane 0
    assert res.errors[0].outcome is Outcome.SEQUENCE_ERROR
    assert lanes(res)[:8] == list(range(8))


def test_exercise4_sub_coarse_delays_do_not_separate_events():
    shim = ARTIQShim()
    t = shim.now_mu()
    for i in range(9):
        shim.at_mu(t + i % 4)               # fine offsets 0,1,2,3,... all under 8 mu
        shim.ttl_on(i)

    # identical to exercise 3: fine offsets are invisible to the lane logic
    assert trace(shim) == [(i, 0) for i in range(9)]
    res = verdict(shim)
    assert res.errors[0].index == 8
    assert res.errors[0].outcome is Outcome.SEQUENCE_ERROR


def test_exercise5_one_coarse_cycle_apart_is_the_repair():
    shim = ARTIQShim()
    t = shim.now_mu()
    for i in range(9):
        shim.at_mu(t + i * 8)
        shim.ttl_on(i)

    assert trace(shim) == [(i, i) for i in range(9)]
    res = verdict(shim)
    assert res.ok
    assert lanes(res) == [0] * 9


def test_exercise6_descending_timestamps_exhaust_lanes():
    shim = ARTIQShim()
    t = shim.now_mu()
    for i in range(9):
        shim.at_mu(t + (8 - i) * 8)
        shim.ttl_on(i)

    assert trace(shim) == [(i, 8 - i) for i in range(9)]
    res = verdict(shim)
    assert res.errors[0].index == 8         # descending is as bad as equal
    assert res.errors[0].outcome is Outcome.SEQUENCE_ERROR
    assert lanes(res)[:8] == list(range(8))


def test_exercise7_parallel_interleaves_submission_order():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():             # branch A
            shim.ttl_on(0)
            shim.delay_mu(8)
            shim.ttl_off(0)
        with shim.sequential():             # branch B
            shim.ttl_on(1)
            shim.delay_mu(8)
            shim.ttl_off(1)

    # both branches start from the same cursor, but submit in source order
    assert trace(shim) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    res = verdict(shim)
    assert res.ok
    assert lanes(res) == [0, 0, 1, 1]


# --- guards: behavior that is correct today and must stay correct ------------------------


def test_ttl_events_are_not_replace():
    # TTL outputs collide on same-channel/same-timestamp; they must not be marked replace.
    shim = ARTIQShim()
    shim.ttl_on(0)
    assert shim.get_events()[0].replace is False


def test_repeated_ttl_at_one_timestamp_is_a_collision():
    shim = ARTIQShim()
    shim.ttl_on(0)
    shim.ttl_on(0)
    assert outcomes(verdict(shim)) == [Outcome.OK, Outcome.COLLISION_ERROR]


# --- `parallel` does not advance the timeline cursor -------------------------------
# In ARTIQ, `with parallel:` leaves the cursor at the END of the longest branch.
# The shim should not restore it to the block's start, which would make everything
# after a parallel block stamped too early.

def test_parallel_advances_cursor_past_the_block():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            shim.ttl_off(0)

    assert shim.now_mu() == 80


def test_event_after_parallel_block_is_stamped_after_it():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            shim.ttl_off(0)
    shim.ttl_on(5)

    # ch5 follows the block, so it must land at coarse 10, not back at 0
    assert trace(shim) == [(0, 0), (0, 10), (5, 10)]


def test_parallel_cursor_takes_the_longest_branch():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():             # short branch
            shim.ttl_on(0)
            shim.delay_mu(8)
        with shim.sequential():             # long branch
            shim.ttl_on(1)
            shim.delay_mu(80)

    assert shim.now_mu() == 80


# --- nested `parallel` should preserve submission order ------------------------

def test_nested_parallel_preserves_submission_order():
    shim = ARTIQShim()
    with shim.parallel():
        shim.ttl_on(1)
        with shim.parallel():
            shim.ttl_on(2)
        shim.ttl_on(3)

    assert [ch for ch, _ in trace(shim)] == [1, 2, 3]


def test_nested_parallel_inside_a_sequential_branch_keeps_order():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(8)
            with shim.parallel():
                shim.ttl_on(1)
            shim.ttl_on(2)

    assert [ch for ch, _ in trace(shim)] == [0, 1, 2]


# --- `dds_set` is marked as a replacement event -------------------------------------------
# sed_model.Event.replace distinguishes channels that overwrite a same-timestamp event (DDS)
# from those that error on collision (most TTLs). The shim should set it, preventing back-to-back
# DDS writes in one coarse cycle to be reported as collisions that hardware would not raise.

def test_dds_events_are_marked_replace():
    shim = ARTIQShim()
    shim.dds_set(0, frequency=1e6)
    assert shim.get_events()[0].replace is True


def test_two_dds_writes_in_one_coarse_cycle_are_not_a_collision():
    shim = ARTIQShim()
    shim.dds_set(0, frequency=1e6)
    shim.dds_set(0, frequency=2e6)

    assert Outcome.COLLISION_ERROR not in outcomes(verdict(shim))


# --- nested parallel/sequential block and timeline cursor tests -----------------

def test_nested_parallel_starts_at_the_enclosing_branch_cursor():
    # The inner block opens 80 mu into the branch, so ch1 belongs at coarse 10, not 0.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            with shim.parallel():
                shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_nested_parallel_advances_the_enclosing_branch_cursor():
    # A parallel block takes as long as its longest branch; the rest of the enclosing
    # sequential branch has to start after it.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            with shim.parallel():
                with shim.sequential():
                    shim.ttl_on(0)
                    shim.delay_mu(80)
            shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_nested_parallel_does_not_disturb_a_later_sibling_branch():
    # Every branch of the outer block starts at the same cursor. The nested parallel in the
    # first branch must not drag the second branch's start time forward with it.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            with shim.parallel():
                with shim.sequential():
                    shim.ttl_on(0)
                    shim.delay_mu(800)
        with shim.sequential():
            shim.ttl_on(1)
            with shim.parallel():
                shim.ttl_on(2)

    assert trace(shim) == [(0, 0), (1, 0), (2, 0)]


def test_nested_parallel_duration_reaches_the_outer_cursor():
    # 80 mu spent inside the inner block still has to show up after the outer block.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            with shim.parallel():
                with shim.sequential():
                    shim.ttl_on(0)
                    shim.delay_mu(80)

    assert shim.now_mu() == 80


def test_sequential_inside_sequential_does_not_rewind():
    # `with sequential:` inside a sequential branch is a no-op in ARTIQ; it must not send the
    # cursor back to the enclosing parallel block's start.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            with shim.sequential():
                shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_nested_parallel_preserves_the_replace_flag():
    # Guard: events buffered by an inner block and flushed through the outer one keep `replace`.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.parallel():
            shim.dds_set(0, frequency=1e6)

    assert [e.replace for e in shim.get_events()] == [True]
