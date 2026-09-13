"""Tests on `reference_timeline`.

1. unit tests pinning the rules the reference claims to encode
2. a differential check against m-labs' own host-side time manager, `artiq/sim/time.py`,
   (skips if no ARTIQ source tree available)

The ARTIQ sim manager rejects backward time (`set_time_mu` raises) and predates negative
delays, so the cross-check runs on the forward-only subset. The rest of the reference is
covered by the unit tests below and by the citations in reference_timeline's docstring.
"""

import random

import pytest

from reference_timeline import Delay, Out, Par, Pulse, Seq, desugar, evaluate, expected


# --- the rules the reference claims to encode ------------------------------------------------


def test_reference_restarts_every_child_of_a_parallel_block():
    events = []
    end = evaluate(Par([Out(0), Delay(80), Out(1)]), 0, events)

    assert events == [(0, 0, False), (1, 0, False)]      # the delay is its own statement
    assert end == 80                                     # ...and still sets the block length


def test_reference_ends_a_block_at_its_longest_branch():
    events = []
    end = evaluate(Par([Seq([Out(0), Delay(8)]), Seq([Out(1), Delay(80)])]), 0, events)

    assert events == [(0, 0, False), (1, 0, False)]
    assert end == 80


def test_reference_never_ends_a_block_before_it_started():
    from reference_timeline import At

    assert evaluate(Par([Seq([At(0)])]), 1000, []) == 1000


def test_reference_ignores_event_timestamps_when_ending_a_block():
    from reference_timeline import At

    events = []
    end = evaluate(Par([Seq([At(800), Out(0), At(0)])]), 0, events)

    assert events == [(0, 800, False)]
    assert end == 0


def test_reference_treats_sequential_as_transparent():
    flat, flat_end = expected(Seq([Out(0), Delay(80), Out(1)]))
    nested, nested_end = expected(Seq([Out(0), Seq([Delay(80), Seq([Out(1)])])]))

    assert flat == nested
    assert flat_end == nested_end


def test_reference_desugars_a_compound_op_into_a_sequential_block():
    assert desugar(Pulse(0, 80)) == Seq([Out(0), Delay(80), Out(0)])

    trace, end = expected(Par([Pulse(0, 1000), Pulse(1, 500)]))
    assert trace == [(0, 0, False), (0, 125, False), (1, 0, False), (1, 62, False)]
    assert end == 1000


# --- differential check against artiq/sim/time.py ---------------------------------------------


def _artiq_sim_available():
    try:
        import l3_gateware

        l3_gateware._ensure_artiq_on_path()
    except Exception:                                    # noqa: BLE001
        pass
    try:
        import artiq.sim.time                            # noqa: F401
    except Exception:                                    # noqa: BLE001
        return False
    return True


requires_artiq_sim = pytest.mark.skipif(
    not _artiq_sim_available(),
    reason="ARTIQ source not available (run scripts/setup_l3.sh)",
)


def forward_only_kernel(rng, depth=3):
    """Random kernel inside the subset artiq.sim.time supports: no at_mu, no negative delay."""

    def node(depth):
        kinds = ["out", "out", "delay", "pulse"]
        if depth > 0:
            kinds += ["seq", "seq", "par", "par"]

        kind = rng.choice(kinds)
        if kind == "out":
            return Out(rng.choice((0, 1, 2, 3)))
        if kind == "delay":
            return Delay(rng.choice((0, 1, 7, 8, 9, 64, 80, 800)))
        if kind == "pulse":
            return Pulse(rng.choice((0, 1, 2, 3)), rng.choice((8, 80, 500, 1000)))

        body = [node(depth - 1) for _ in range(rng.randint(0, 3))]
        return Seq(body) if kind == "seq" else Par(body)

    return Seq([node(depth) for _ in range(rng.randint(1, 4))])


def artiq_sim_run(node):
    """Run `node` through m-labs' own time manager; return (events, end_cursor) in mu."""
    from artiq.sim.time import Manager

    manager = Manager()
    events = []

    def walk(n):
        n = desugar(n)
        if isinstance(n, Out):
            events.append((n.channel, int(manager.get_time_mu()), n.replace))
        elif isinstance(n, Delay):
            manager.take_time_mu(n.mu)
        elif isinstance(n, (Seq, Par)):
            manager.enter_sequential() if isinstance(n, Seq) else manager.enter_parallel()
            for child in n.body:
                walk(child)
            manager.exit()
        else:
            raise TypeError(f"outside the artiq.sim subset: {n!r}")

    walk(node)
    return events, int(manager.get_time_mu())


@requires_artiq_sim
def test_reference_matches_the_artiq_sim_time_manager(fuzz_kernels, fuzz_seed):
    rng = random.Random(fuzz_seed)
    for _ in range(min(fuzz_kernels, 500)):
        kernel = forward_only_kernel(rng)

        mine = []
        my_end = evaluate(kernel, 0, mine)
        theirs, their_end = artiq_sim_run(kernel)

        assert mine == theirs, f"event times differ on:\n{kernel!r}"
        assert my_end == their_end, f"final cursor differs on:\n{kernel!r}"
