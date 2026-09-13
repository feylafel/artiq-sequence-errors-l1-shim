from sed_model import Event, SEDModel, SimResult
from typing import List, Tuple, Optional
from contextlib import contextmanager


class ARTIQShim:
    def __init__(self):
        self.current_time_mu = 0
        self.events: List[Tuple[int, int, bool]] = []
        self.parallel_events: Optional[List[Tuple[int, int, bool]]] = None
        self.parallel_timestamp: Optional[int] = None
        self.branch_time_mu: Optional[int] = None
        self._branch_end_times: List[int] = []

    def now_mu(self) -> int:
        if self.branch_time_mu is not None: #handle now_mu for branch cases
            return self.branch_time_mu
        return self.current_time_mu

    def delay_mu(self, cycles: int) -> None:
        if self.branch_time_mu is not None:
            self.branch_time_mu += cycles
            print(f" branch_time_mu now = {self.branch_time_mu}")
        else:
            self.current_time_mu += cycles
            print(f" current_time_mu now = {self.current_time_mu}")

    def at_mu(self, timestamp: int) -> None:
        if self.branch_time_mu is not None:
            self.branch_time_mu = timestamp
        else:
            self.current_time_mu = timestamp

    def record(self, channel: int, replace:bool=False) -> None:
        if self.parallel_events is not None:
            if self.branch_time_mu is not None:
                ts = self.branch_time_mu
            else:
                ts = self.parallel_timestamp
            self.parallel_events.append((channel, ts, replace))
        else:
            coarse_ts = self.current_time_mu >> 3
            self.events.append((channel, coarse_ts, replace))

    def ttl_on(self, channel: int) -> None:
        self.record(channel)

    def ttl_off(self, channel: int) -> None:
        self.record(channel)

    def dds_set(self, channel: int, **kwargs) -> None:
        self.record(channel, replace=True)

    #parallel context manager
    @contextmanager
    def parallel(self):
        block_time = self.current_time_mu

        prev_parallel_events = self.parallel_events
        prev_parallel_timestamp = self.parallel_timestamp
        prev_branch_time = self.branch_time_mu
        prev_branch_end_times = self._branch_end_times

        self.parallel_events = []
        self.parallel_timestamp = block_time
        self.branch_time_mu = block_time
        self._branch_end_times = []

        try:
            yield self
        finally:
            max_time = block_time
            for end_time in self._branch_end_times:
                if end_time > max_time:
                    max_time = end_time

            for channel, ts, replace in self.parallel_events:
                if ts > max_time:
                    max_time = ts

            if self.branch_time_mu > max_time:
                max_time = self.branch_time_mu

            if prev_parallel_events is not None:
                for channel, ts, replace in self.parallel_events:
                    prev_parallel_events.append((channel, ts, replace))
            else:
                for channel, ts, replace in self.parallel_events:
                    coarse_ts = ts >> 3
                    self.events.append((channel, coarse_ts, replace))

            if self.branch_time_mu is not None and self.branch_time_mu > max_time:
                max_time = self.branch_time_mu

            print(f"PARALLEL: previous current_time_mu = {self.current_time_mu}")
            self.current_time_mu = max_time
            print(f"PARALLEL: current_time_mu after = {self.current_time_mu}")
            self.parallel_events = prev_parallel_events
            self.parallel_timestamp = prev_parallel_timestamp
            self.branch_time_mu = prev_branch_time
            self._branch_end_times = prev_branch_end_times


    @contextmanager
    def sequential(self):
        if self.parallel_events is not None:
            prev_branch_time = self.branch_time_mu
            self.branch_time_mu = self.parallel_timestamp
            try:
                yield self
            finally:
                self._branch_end_times.append(self.branch_time_mu)
                self.branch_time_mu = prev_branch_time
        else:
            yield self

    def get_events(self) -> List[Event]:
        return [Event(channel=ch, coarse_ts=ts, replace=replace) for ch, ts, replace in self.events]   #convert recorded events to events defined in SEDModel

    def verify(self, **kwargs) -> SimResult:
        return SEDModel(**kwargs).run(self.get_events())   #feed events to SEDModel

