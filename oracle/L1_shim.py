"""Layer 1: ARTIQ kernel-API shim that records an RTIO submission trace.

Internally the timeline is an explicit stack of frames (sequential / parallel).
The external API stays ARTIQ-shaped (`with shim.parallel():`, device objects, etc.).
"""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sed_model import Event, SEDModel, SimResult

COARSE_SHIFT = 3


class ShimUsageError(Exception):
    """Raised for shim misuse that would silently diverge from ARTIQ."""


@dataclass
class _Frame:
    mode: str  # "sequential" | "parallel"
    start: int
    cursor: int
    end: int
    owner: Optional[object]  # the Python frame that owns `with ...:`


class _BlockContext(AbstractContextManager):
    """Pushes/pops a timeline frame; captures the `with`-owner frame for R13."""

    def __init__(self, shim: "ARTIQShim", mode: str) -> None:
        self._shim = shim
        self._mode = mode

    def __enter__(self) -> "ARTIQShim":
        self._shim._enter(self._mode, sys._getframe(1))
        return self._shim

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._shim._exit(aborted=exc_type is not None)
        return False


class TTLDevice:
    def __init__(self, shim: "ARTIQShim", channel: int) -> None:
        self._shim = shim
        self._channel = channel

    def on(self) -> None:
        self._shim.ttl_on(self._channel)

    def off(self) -> None:
        self._shim.ttl_off(self._channel)

    def pulse_mu(self, duration: int) -> None:
        self._shim.ttl_pulse_mu(self._channel, duration)


class DDSDevice:
    def __init__(self, shim: "ARTIQShim", channel: int) -> None:
        self._shim = shim
        self._channel = channel

    def set(self, **kwargs) -> None:
        self._shim.dds_set(self._channel, **kwargs)


class ARTIQShim:
    def __init__(self) -> None:
        # Root sequential frame: top-level timeline starts at 0.
        self._stack: List[_Frame] = [
            _Frame(mode="sequential", start=0, cursor=0, end=0, owner=None)
        ]
        # Append-only, program order; timestamps stay in mu until get_events().
        self._events: List[Tuple[int, int, bool]] = []
        self._channel_kind: Dict[int, str] = {}
        self._ttl: Dict[int, TTLDevice] = {}
        self._dds: Dict[int, DDSDevice] = {}

    # --- stack ---------------------------------------------------------------

    def _enter(self, mode: str, owner: object) -> None:
        parent = self._stack[-1]
        start = parent.start if parent.mode == "parallel" else parent.cursor
        self._stack.append(
            _Frame(mode=mode, start=start, cursor=start, end=start, owner=owner)
        )

    def _exit(self, aborted: bool) -> None:
        frame = self._stack.pop()
        if aborted:
            # R11: unwind only; already-submitted events stay; contribute nothing.
            return
        parent = self._stack[-1]
        result = frame.end if frame.mode == "parallel" else frame.cursor
        if parent.mode == "parallel":
            parent.end = max(parent.end, result)
        else:
            parent.cursor = result

    def parallel(self) -> _BlockContext:
        return _BlockContext(self, "parallel")

    def sequential(self) -> _BlockContext:
        return _BlockContext(self, "sequential")

    # --- cursor --------------------------------------------------------------

    def now_mu(self) -> int:
        top = self._stack[-1]
        # R6: in a bare parallel body the cursor does not advance.
        if top.mode == "parallel":
            return top.start
        return top.cursor

    def delay_mu(self, cycles: int) -> None:
        top = self._stack[-1]
        if top.mode == "parallel":
            self._guard_bare_parallel_time()
            # Each bare statement starts at block entry (compiler: at_mu(start_mu)).
            top.end = max(top.end, top.start + cycles)
            return
        top.cursor += cycles

    def at_mu(self, timestamp: int) -> None:
        top = self._stack[-1]
        if top.mode == "parallel":
            self._guard_bare_parallel_time()
            top.end = max(top.end, timestamp)
            return
        top.cursor = timestamp

    def _guard_bare_parallel_time(self) -> None:
        # R13: time-taking from a helper frame inside a bare parallel body.
        top = self._stack[-1]
        caller = sys._getframe(2)  # caller of delay_mu / at_mu
        if caller is not top.owner:
            raise ShimUsageError(
                "time-taking call from a helper in a bare parallel body; "
                "wrap the branch in `with sequential:`"
            )

    # --- events --------------------------------------------------------------

    def _record(self, channel: int, replace: bool) -> None:
        top = self._stack[-1]
        ts = top.start if top.mode == "parallel" else top.cursor
        self._events.append((channel, ts, replace))

    def _use_channel(self, channel: int, kind: str) -> None:
        prev = self._channel_kind.get(channel)
        if prev is None:
            self._channel_kind[channel] = kind
        elif prev != kind:
            raise ShimUsageError(
                f"channel {channel} already used as {prev}, cannot use as {kind}"
            )

    def ttl_on(self, channel: int) -> None:
        self._use_channel(channel, "ttl")
        self._record(channel, False)

    def ttl_off(self, channel: int) -> None:
        self._use_channel(channel, "ttl")
        self._record(channel, False)

    def ttl_pulse_mu(self, channel: int, duration: int) -> None:
        # R12: compound device op = implicit `with sequential:`
        self._use_channel(channel, "ttl")
        with self.sequential():
            self._record(channel, False)
            self.delay_mu(duration)
            self._record(channel, False)

    def dds_set(self, channel: int, **kwargs) -> None:
        self._use_channel(channel, "dds")
        self._record(channel, True)

    # --- devices -------------------------------------------------------------

    def ttl(self, channel: int) -> TTLDevice:
        self._use_channel(channel, "ttl")
        dev = self._ttl.get(channel)
        if dev is None:
            dev = TTLDevice(self, channel)
            self._ttl[channel] = dev
        return dev

    def dds(self, channel: int) -> DDSDevice:
        self._use_channel(channel, "dds")
        dev = self._dds.get(channel)
        if dev is None:
            dev = DDSDevice(self, channel)
            self._dds[channel] = dev
        return dev

    # --- oracle surface ------------------------------------------------------

    def get_events(self) -> List[Event]:
        # R3: mu -> coarse conversion happens exactly once, on the way out.
        return [
            Event(channel=ch, coarse_ts=mu >> COARSE_SHIFT, replace=replace)
            for ch, mu, replace in self._events
        ]

    def verify(self, **kwargs) -> SimResult:
        return SEDModel(**kwargs).run(self.get_events())