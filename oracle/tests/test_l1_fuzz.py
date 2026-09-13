"""Differential fuzz test against `reference_timeline.evaluate`.

Build random nested parallel/sequential kernels, runs each one both ways, and shrinks any
disagreement to a minimal kernel that it prints as copy-pasteable source.

    pytest tests/test_l1_fuzz.py                            # 500 kernels, seed 0
    pytest tests/test_l1_fuzz.py --fuzz-kernels 20000       # before calling it done
    pytest tests/test_l1_fuzz.py --fuzz-seed 7              # a different sample

Seeded, to reproduce a failure.

Warning: This fuzzer was vibed.
"""

import random

import pytest

from L1_shim import ARTIQShim
from reference_timeline import (
    At, Delay, Out, Par, Pulse, Seq, expected, run_on_shim, to_source,
)

pytestmark = pytest.mark.acceptance

# One flat RTIO channel namespace, partitioned by device type (R15).
TTL_CHANNELS = (0, 1, 2, 3, 4, 5)
DDS_CHANNELS = (6, 7, 8, 9)

DELAYS = (0, 1, 7, 8, 9, 64, 80, 800, -8, -80)
TIMES = (0, 1, 8, 24, 100, 800, 1000)
PULSES = (8, 80, 500, 1000)


# --- generation -------------------------------------------------------------------------------


def random_node(rng, depth):
    kinds = ["out", "out", "delay", "at", "pulse"]
    if depth > 0:
        kinds += ["seq", "seq", "par", "par"]

    kind = rng.choice(kinds)
    if kind == "out":
        if rng.random() < 0.25:
            return Out(rng.choice(DDS_CHANNELS), replace=True)
        return Out(rng.choice(TTL_CHANNELS))
    if kind == "delay":
        return Delay(rng.choice(DELAYS))
    if kind == "at":
        return At(rng.choice(TIMES))
    if kind == "pulse":
        return Pulse(rng.choice(TTL_CHANNELS), rng.choice(PULSES))

    body = [random_node(rng, depth - 1) for _ in range(rng.randint(0, 3))]
    return Seq(body) if kind == "seq" else Par(body)


def random_kernel(rng, depth=3):
    """A kernel is a sequence of top-level statements -- the timeline starts sequential."""
    return Seq([random_node(rng, depth) for _ in range(rng.randint(1, 4))])


# --- comparison -------------------------------------------------------------------------------


def disagreement(node, device_objects=True, shim_factory=ARTIQShim):
    """How the shim differs from the reference on `node`, or None if they agree."""
    shim = shim_factory()
    try:
        run_on_shim(node, shim, device_objects)
    except Exception as exc:                      # noqa: BLE001 -- any failure is a finding
        return f"shim raised {type(exc).__name__}: {exc}"

    want_trace, want_end = expected(node)
    got_trace = [(e.channel, e.coarse_ts, e.replace) for e in shim.get_events()]
    if got_trace != want_trace:
        return f"trace\n     shim: {got_trace}\nreference: {want_trace}"

    got_end = shim.now_mu()
    if got_end != want_end:
        return f"final now_mu()\n     shim: {got_end}\nreference: {want_end}"
    return None


# --- shrinking --------------------------------------------------------------------------------


def candidates(node):
    """Smaller kernels to try, most aggressive first."""
    if isinstance(node, (Seq, Par)):
        for child in node.body:
            yield child                                       # replace the block with a child
        for i in range(len(node.body)):
            yield type(node)(node.body[:i] + node.body[i + 1:])          # drop a child
        for i, child in enumerate(node.body):
            for smaller in candidates(child):
                yield type(node)(node.body[:i] + [smaller] + node.body[i + 1:])
    elif isinstance(node, Delay) and node.mu not in (0, 8):
        yield Delay(8)
    elif isinstance(node, At) and node.mu not in (0, 8):
        yield At(8)
    elif isinstance(node, Pulse) and node.mu != 8:
        yield Pulse(node.channel, 8)


def shrink(node, still_fails, budget=2000):
    """Greedily simplify `node` while `still_fails(node)` holds."""
    improved = True
    while improved and budget > 0:
        improved = False
        for candidate in candidates(node):
            budget -= 1
            if budget <= 0:
                break
            if still_fails(candidate):
                node, improved = candidate, True
                break
    return node


def report(node, device_objects):
    minimal = shrink(node, lambda n: disagreement(n, device_objects) is not None)
    surface = "device objects" if device_objects else "function calls"
    return (
        f"shim disagrees with reference_timeline ({surface}):\n\n"
        f"{to_source(minimal, device_objects=device_objects)}\n\n"
        f"{disagreement(minimal, device_objects)}\n\n"
        f"See notes/issue1.md for the rules; add this kernel to REGRESSIONS once fixed."
    )


# --- the tests ---------------------------------------------------------------------------------


def test_shim_matches_the_reference_on_random_kernels(fuzz_kernels, fuzz_seed):
    rng = random.Random(fuzz_seed)
    for i in range(fuzz_kernels):
        device_objects = bool(i % 2)              # both API surfaces, alternating
        node = random_kernel(rng)
        if disagreement(node, device_objects) is not None:
            pytest.fail(report(node, device_objects))


# Shrunk fuzz failures, kept as fixed cases. Name them after what they broke.
REGRESSIONS = {
    # "bare delay in a parallel body": Par([Out(0), Delay(80), Out(1)]),
}


@pytest.mark.parametrize("name", sorted(REGRESSIONS))
def test_regressions(name):
    node = REGRESSIONS[name]
    for device_objects in (True, False):
        assert disagreement(node, device_objects) is None, report(node, device_objects)


def test_the_generator_reaches_the_shapes_that_matter(fuzz_seed):
    """A fuzzer that only emits flat kernels would pass while proving nothing."""
    rng = random.Random(fuzz_seed)
    kernels = [random_kernel(rng) for _ in range(500)]

    def shapes(node, inside_par=False):
        found = set()
        if isinstance(node, Par):
            found.add("parallel")
            for child in node.body:
                if isinstance(child, Delay):
                    found.add("bare delay in a parallel body")
                if isinstance(child, At):
                    found.add("bare at_mu in a parallel body")
                if isinstance(child, Pulse):
                    found.add("compound op in a parallel body")
                if isinstance(child, Par):
                    found.add("parallel directly in parallel")
                if isinstance(child, Seq):
                    found.add("sequential branch")
                found |= shapes(child, inside_par=True)
        elif isinstance(node, Seq):
            if inside_par:
                found.add("sequential branch")
            for child in node.body:
                if isinstance(child, Par):
                    found.add("parallel nested in a sequential branch")
                found |= shapes(child, inside_par=False)
        elif isinstance(node, Out) and node.replace:
            found.add("dds write")
        return found

    seen = set()
    for kernel in kernels:
        seen |= shapes(kernel)

    required = {
        "parallel",
        "sequential branch",
        "bare delay in a parallel body",
        "bare at_mu in a parallel body",
        "compound op in a parallel body",
        "parallel directly in parallel",
        "parallel nested in a sequential branch",
        "dds write",
    }
    assert required <= seen, f"generator never produced: {sorted(required - seen)}"
