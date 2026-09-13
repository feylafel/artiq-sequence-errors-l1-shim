"""Regression tests for the SED functional oracle (sed_model.py).

These pin the exact outcomes the model must reproduce from ARTIQ's lane-distribution spec.
Run with `pytest` (repo root is put on sys.path by conftest.py).
"""

from sed_model import Event, Outcome, SEDModel


def _ts(trace, prefix="ttl"):
    return [Event(channel=ch, coarse_ts=ts, label=f"{prefix}{ch}") for ch, ts in trace]


def outcomes(result):
    return [s.outcome for s in result.steps]


def lanes(result):
    return [s.lane for s in result.steps]


# --- the canonical example: 9 same-timestamp events, 8 lanes ------------------------------

def test_ninth_same_timestamp_event_is_sequence_error():
    events = [Event(channel=c, coarse_ts=1000) for c in range(9)]
    res = SEDModel(lanes=8).run(events)

    assert not res.ok
    assert len(res.errors) == 1
    err = res.errors[0]
    assert err.index == 8                       # silent failure on the 9th event
    assert err.outcome is Outcome.SEQUENCE_ERROR
    assert err.lane is None
    # events 0..7 each fill a distinct lane, every one a forced switch
    assert lanes(res)[:8] == list(range(8))
    assert res.steps[0].switched is False
    assert all(res.steps[i].switched for i in range(1, 9))


def test_exactly_lane_count_same_timestamp_is_ok():
    # 8 events at one coarse ts exactly fills the 8 lanes; no error.
    res = SEDModel(lanes=8).run([Event(channel=c, coarse_ts=1000) for c in range(8)])
    assert res.ok
    assert lanes(res) == list(range(8))


def test_lane_count_is_configurable():
    # With 4 lanes, the 5th equal-timestamp event errors instead of the 9th.
    res = SEDModel(lanes=4).run([Event(channel=c, coarse_ts=1000) for c in range(5)])
    assert [s.outcome for s in res.steps] == [Outcome.OK] * 4 + [Outcome.SEQUENCE_ERROR]


# --- the fixed (repaired) schedule --------------------------------------------------------

def test_strictly_increasing_timestamps_never_error():
    # One coarse cycle apart: all land in lane 0, monotonic, no switches.
    events = [Event(channel=c, coarse_ts=1000 + c) for c in range(9)]
    res = SEDModel(lanes=8).run(events)
    assert res.ok
    assert lanes(res) == [0] * 9
    assert not any(s.switched for s in res.steps)


def test_descending_timestamps_exhaust_lanes():
    # Strictly descending timestamps force a switch every event, same as equal ones.
    events = [Event(channel=c, coarse_ts=2000 - c) for c in range(9)]
    res = SEDModel(lanes=8).run(events)
    assert outcomes(res)[-1] is Outcome.SEQUENCE_ERROR
    assert all(s.switched for s in res.steps[1:])


# --- collision and underflow paths --------------------------------------------------------

def test_same_channel_same_timestamp_collides_without_replace():
    events = [Event(channel=5, coarse_ts=1000), Event(channel=5, coarse_ts=1000)]
    res = SEDModel(lanes=8).run(events)
    assert outcomes(res) == [Outcome.OK, Outcome.COLLISION_ERROR]


def test_replace_channel_does_not_collide():
    # DDS-style replacement: same channel + coarse ts is allowed.
    events = [Event(channel=5, coarse_ts=1000, replace=True),
              Event(channel=5, coarse_ts=1000, replace=True)]
    res = SEDModel(lanes=8).run(events)
    # second event is not a collision; it follows normal lane rules (switches, same ts) ->
    # lands on lane 1 since ts is not strictly above lane 0's last write.
    assert outcomes(res)[0] is Outcome.OK
    assert outcomes(res)[1] is not Outcome.COLLISION_ERROR


def test_timestamp_at_or_below_minimum_underflows():
    res = SEDModel(lanes=8, minimum_coarse_timestamp=1000).run([Event(channel=0, coarse_ts=1000)])
    assert outcomes(res) == [Outcome.UNDERFLOW]


# --- spreading changes lane utilisation, not correctness here -----------------------------

def test_spread_advances_lane_on_high_watermark():
    # Tiny lane depth + spread: events at strictly increasing ts still advance lanes once the
    # current lane hits the high watermark, instead of stacking in one lane.
    events = [Event(channel=0, coarse_ts=1000 + i) for i in range(4)]
    res = SEDModel(lanes=8, lane_depth=2, high_watermark=1, enable_spread=True).run(events)
    assert res.ok
    # without spread these would all be lane 0; with spread the lane advances.
    assert len(set(lanes(res))) > 1


# --- the shipped example traces stay locked to their kernels ------------------------------

def test_example_traces_match_documented_outcomes():
    from examples.sequence_error_kernel import EVENT_TRACE, FIXED_TRACE

    bad = SEDModel(lanes=8).run(_ts(EVENT_TRACE))
    assert not bad.ok and bad.errors[0].index == 8

    fixed = SEDModel(lanes=8).run(_ts(FIXED_TRACE))
    assert fixed.ok
