"""Layer 2: a thin functional model of ARTIQ's RTIO Scalable Event Dispatcher (SED).

This is the verification oracle for sequence/collision errors. It is a plain-Python
re-implementation of the lane-distribution state machine in ARTIQ gateware:

    artiq/gateware/rtio/sed/lane_distributor.py

Each event is an RTIO output submitted, in program (submission) order, to one of N FIFO
"lanes". Within a lane, coarse timestamps must be strictly increasing. The SED keeps a
`current_lane` cursor and the last coarse timestamp written to each lane, and chooses a lane
per the rules below. A sequence error is when placement fails.

Faithful mapping to the gateware signals (see lane_distributor.py):

    timestamp_above_last  -> ts > last_lane_ts[current_lane]      # stay in current lane?
    force_laneB           -> enable_spread & (high_watermark | lane_full)
    use_lane              -> current_lane         if (timestamp_above_last & ~force_laneB)
                             (current_lane + 1)%N  otherwise
    timestamp_above_min   -> ts > minimum_coarse_timestamp        # else underflow
    timestamp_above_lane  -> ts > last_lane_ts[use_lane]          # else SEQUENCE ERROR

The model deliberately works on a stream of already-extracted events
`(channel, coarse_timestamp)`; producing that stream from a kernel is Layer 1 (see
examples/sequence_error_kernel.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Outcome(Enum):
    OK = "ok"
    SEQUENCE_ERROR = "sequence_error"
    COLLISION_ERROR = "collision_error"
    UNDERFLOW = "underflow"


@dataclass
class Event:
    """One RTIO output event, in submission order.

    coarse_ts is the *coarse* RTIO timestamp (fine timestamp // coarse_ratio). Sequence-error
    behavior depends only on coarse timestamps, channel, and submission order.
    """
    channel: int
    coarse_ts: int
    # Whether this channel replaces same-timestamp events (e.g. DDS) vs. errors on collision
    # (most TTL outputs). Only used for collision detection.
    replace: bool = False
    label: str = ""


@dataclass
class StepResult:
    index: int
    event: Event
    outcome: Outcome
    lane: Optional[int]          # lane the event landed in (None if it errored/underflowed)
    switched: bool               # did the SED advance to the next lane for this event?


@dataclass
class SimResult:
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.outcome is Outcome.OK for s in self.steps)

    @property
    def errors(self) -> list[StepResult]:
        return [s for s in self.steps if s.outcome is not Outcome.OK]

    def summary(self) -> str:
        lines = []
        for s in self.steps:
            tag = "" if s.outcome is Outcome.OK else f"  <-- {s.outcome.value.upper()}"
            lane = "-" if s.lane is None else s.lane
            sw = " (switched)" if s.switched else ""
            name = s.event.label or f"ch{s.event.channel}"
            lines.append(
                f"  [{s.index:>3}] {name:<14} coarse_ts={s.event.coarse_ts:<6} "
                f"lane={lane}{sw}{tag}"
            )
        head = "OK" if self.ok else f"{len(self.errors)} error(s)"
        return f"SED simulation: {head}\n" + "\n".join(lines)


class SEDModel:
    """Functional model of the SED lane distributor.

    Parameters mirror the gateware build options:
      lanes            -> number of FIFO lanes (ARTIQ default: 8)
      lane_depth       -> per-lane FIFO depth, for the "nearly full" watermark
      enable_spread    -> SED event spreading (advance on high watermark)
      high_watermark   -> occupancy at which spreading advances the lane

    Note: this model does not drain lanes over time (no egress/execution model). That is the
    conservative, static-friendly assumption: it captures the worst case the gateware can hit
    for a given submission stream. A timed egress model can be added later if needed.
    """

    def __init__(
        self,
        lanes: int = 8,
        lane_depth: int = 128,
        enable_spread: bool = False,
        high_watermark: Optional[int] = None,
        minimum_coarse_timestamp: int = -1,
    ):
        self.lanes = lanes
        self.lane_depth = lane_depth
        self.enable_spread = enable_spread
        self.high_watermark = high_watermark if high_watermark is not None else lane_depth - 1
        self.minimum_coarse_timestamp = minimum_coarse_timestamp

    def run(self, events: list[Event]) -> SimResult:
        N = self.lanes
        current_lane = 0
        last_lane_ts = [self.minimum_coarse_timestamp] * N   # last coarse ts written per lane
        occupancy = [0] * N                                  # events resident per lane
        last_channel_ts: dict[int, int] = {}                 # for collision detection

        result = SimResult()
        for i, ev in enumerate(events):
            ts = ev.coarse_ts

            # --- collision: same channel, same coarse ts, replacement disabled ---
            prev = last_channel_ts.get(ev.channel)
            if prev is not None and ts == prev and not ev.replace:
                result.steps.append(StepResult(i, ev, Outcome.COLLISION_ERROR, None, False))
                continue

            # --- lane selection (lane_distributor.py) ---
            timestamp_above_last = ts > last_lane_ts[current_lane]
            lane_full = occupancy[current_lane] >= self.lane_depth
            high = occupancy[current_lane] >= self.high_watermark
            force_laneB = self.enable_spread and (high or lane_full)

            if force_laneB or not timestamp_above_last:
                use_lane = (current_lane + 1) % N
                switched = True
            else:
                use_lane = current_lane
                switched = False

            # --- underflow check against the system minimum ---
            if not ts > self.minimum_coarse_timestamp:
                result.steps.append(StepResult(i, ev, Outcome.UNDERFLOW, None, switched))
                continue

            # --- write or sequence error ---
            if ts > last_lane_ts[use_lane]:
                last_lane_ts[use_lane] = ts
                occupancy[use_lane] += 1
                current_lane = use_lane
                last_channel_ts[ev.channel] = ts
                result.steps.append(StepResult(i, ev, Outcome.OK, use_lane, switched))
            else:
                result.steps.append(
                    StepResult(i, ev, Outcome.SEQUENCE_ERROR, None, switched)
                )

        return result


if __name__ == "__main__":
    # Demo: 9 events at the SAME coarse timestamp on distinct TTL channels, 8 lanes.
    # Events 0..7 fill lanes 0..7 (each switch because ts is never strictly greater);
    # event 8 wraps back to lane 0 where last_ts == ts -> SEQUENCE ERROR on the 9th event.
    events = [Event(channel=c, coarse_ts=1000, label=f"ttl{c}.on") for c in range(9)]
    print(SEDModel(lanes=8).run(events).summary())
