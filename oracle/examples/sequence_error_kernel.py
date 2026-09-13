"""A minimal ARTIQ kernel that deterministically provokes an RTIO sequence error,
plus the event trace it produces so the trace can be fed to sed_model.py without hardware.

The rule: within one coarse RTIO cycle you can submit at most `lanes` (default 8) events whose
coarse timestamps are equal or descending, because each such event forces the SED to the next
lane. The 9th event at the same coarse timestamp wraps back to a lane that already holds that
timestamp -> sequence error. Crucially, the kernel raises NO exception; it runs to completion
and the error is only visible in the core log. That silent failure is the whole motivation.

This file is illustrative ARTIQ Python (it imports artiq, which need not be installed here).
The EVENT_TRACE / FIXED_TRACE constants below are what Layer 1 (API-level capture) would
extract; they let you exercise the Layer 2 oracle today.
"""

# --- The ARTIQ kernel (would run on a real core device) -----------------------------------
try:
    from artiq.experiment import EnvExperiment, kernel, delay_mu
except ImportError:  # artiq not installed; the kernel is here for illustration only
    EnvExperiment = object

    def kernel(fn):
        return fn

    def delay_mu(_):        # no-op stand-in so run_fixed is importable/callable without artiq
        pass


N = 9               # one more than the default lane count (8) -> guaranteed sequence error
COARSE_RATIO = 8    # fine mu per coarse cycle (1 ns fine, 8 ns coarse on a typical build)


class SequenceErrorDemo(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.ttl = [self.get_device(f"ttl{i}") for i in range(N)]

    """
     Note: These kernels aren't meant to be ran. They are just illustrative.
    """

    @kernel
    def run(self):
        self.core.reset()
        # All N events share the same timeline cursor -> same coarse timestamp.
        # No delay() between them, so the SED cannot keep them monotonically increasing.
        for i in range(N):
            self.ttl[i].on()        # event submitted at the current (unchanged) timestamp
        # Returns normally. No exception. The sequence error is silent.

    @kernel
    def run_fixed(self):
        """ Insert one coarse cycle between events so each lands strictly later.
        Same observable choreography (all pulses ~back to back), but no sequence error."""
        self.core.reset()
        for i in range(N):
            self.ttl[i].on()
            delay_mu(COARSE_RATIO)  # advance one coarse cycle -> strictly increasing ts


# --- Extracted event traces (what Layer 1 capture yields) ---------------------------------
# Format consumed by sed_model.Event: (channel, coarse_ts).
# Bad: all 9 at coarse_ts=1000 -> sequence error on event index 8.
EVENT_TRACE = [(ch, 1000) for ch in range(N)]

# Fixed: each event one coarse cycle later -> strictly increasing, no error.
FIXED_TRACE = [(ch, 1000 + ch) for ch in range(N)]


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sed_model import Event, SEDModel

    def to_events(trace, prefix):
        return [Event(channel=ch, coarse_ts=ts, label=f"{prefix}{ch}.on")
                for ch, ts in trace]

    print("=== BAD kernel (run) ===")
    print(SEDModel(lanes=8).run(to_events(EVENT_TRACE, "ttl")).summary())
    print("\n=== FIXED kernel (run_fixed) ===")
    print(SEDModel(lanes=8).run(to_events(FIXED_TRACE, "ttl")).summary())
