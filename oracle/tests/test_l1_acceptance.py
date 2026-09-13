"""Layer 1 conformance suite.

Every test corresponds to a numbered rule in `notes/issue1.md`, and every rule
there has a test here.

The rules are transcribed from ARTIQ's own source.

    pytest tests/test_l1_acceptance.py
"""

import pytest

import L1_shim
from L1_shim import ARTIQShim



# --- helpers --------------------------------------------------------------------------------
# Written so the module still imports (and every test fails independently, with a legible
# message) while the shim is missing the API these rules require.


def trace(shim):
    """The (channel, coarse_ts) stream, in submission order."""
    return [(e.channel, e.coarse_ts) for e in shim.get_events()]


def full_trace(shim):
    return [(e.channel, e.coarse_ts, e.replace) for e in shim.get_events()]


def usage_error():
    exc = getattr(L1_shim, "ShimUsageError", None)
    if exc is None:
        pytest.fail("L1_shim defines no ShimUsageError")
    return exc


def dev(shim, kind, channel):
    factory = getattr(shim, kind, None)
    if factory is None:
        pytest.fail(f"shim has no `{kind}()` device accessor")
    return factory(channel)


# --- R1: the cursor -------------------------------------------------------------------------


def test_R1_now_mu_starts_at_zero():
    assert ARTIQShim().now_mu() == 0


def test_R1_delay_mu_advances_the_cursor():
    shim = ARTIQShim()
    shim.delay_mu(80)
    shim.delay_mu(8)
    assert shim.now_mu() == 88


def test_R1_delay_mu_accepts_a_negative_delay():
    # ARTIQ allows negative delays; the shim must not clamp or reject them.
    shim = ARTIQShim()
    shim.delay_mu(80)
    shim.delay_mu(-64)
    assert shim.now_mu() == 16


def test_R1_at_mu_sets_the_cursor_including_backwards():
    shim = ARTIQShim()
    shim.at_mu(800)
    assert shim.now_mu() == 800
    shim.at_mu(80)
    assert shim.now_mu() == 80


# --- R2: submission order -------------------------------------------------------------------


def test_R2_events_are_recorded_in_program_order_across_nesting():
    shim = ARTIQShim()
    with shim.parallel():
        shim.ttl_on(1)
        with shim.parallel():
            shim.ttl_on(2)
        shim.ttl_on(3)

    assert [ch for ch, _ in trace(shim)] == [1, 2, 3]


def test_R2_the_trace_is_never_sorted_by_timestamp():
    # Descending timestamps are exactly what provokes a sequence error; sorting would hide it.
    shim = ARTIQShim()
    for i in range(3):
        shim.at_mu((3 - i) * 8)
        shim.ttl_on(i)

    assert trace(shim) == [(0, 3), (1, 2), (2, 1)]


# --- R3: mu -> coarse -----------------------------------------------------------------------


def test_R3_coarse_is_a_single_floor_shift():
    shim = ARTIQShim()
    for t in (0, 7, 8, 15, 16):
        shim.at_mu(t)
        shim.ttl_on(t)

    assert trace(shim) == [(0, 0), (7, 0), (8, 1), (15, 1), (16, 2)]


def test_R3_events_inside_a_block_are_converted_exactly_once():
    # Guards the double-shift / no-shift bug class: same event, same answer, inside a block.
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.at_mu(24)
            shim.ttl_on(0)

    assert trace(shim) == [(0, 3)]


def test_R3_negative_timestamps_floor():
    shim = ARTIQShim()
    shim.at_mu(-1)
    shim.ttl_on(0)

    assert trace(shim) == [(0, -1)]


# --- R4: the replace flag -------------------------------------------------------------------


def test_R4_ttl_output_is_not_a_replacement():
    shim = ARTIQShim()
    shim.ttl_on(0)
    assert shim.get_events()[0].replace is False


def test_R4_dds_set_is_a_replacement():
    shim = ARTIQShim()
    shim.dds_set(4, frequency=1e6)
    assert shim.get_events()[0].replace is True


def test_R4_replace_survives_nested_blocks():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.parallel():
            shim.dds_set(4, frequency=1e6)

    assert full_trace(shim) == [(4, 0, True)]


# --- R5: `sequential` is a no-op ------------------------------------------------------------
# artiq_ir_generator.py:925-927 -- visit(body), nothing else, at any depth.


def test_R5_sequential_at_top_level_is_transparent():
    shim = ARTIQShim()
    shim.ttl_on(0)
    with shim.sequential():
        shim.delay_mu(80)
        shim.ttl_on(1)
    shim.ttl_on(2)

    assert trace(shim) == [(0, 0), (1, 10), (2, 10)]
    assert shim.now_mu() == 80


def test_R5_sequential_inside_sequential_does_not_rewind():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            with shim.sequential():
                shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_R5_sequential_delimits_a_branch_inside_parallel():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(8)
            shim.ttl_off(0)
        with shim.sequential():
            shim.ttl_on(1)
            shim.delay_mu(8)
            shim.ttl_off(1)

    assert trace(shim) == [(0, 0), (0, 1), (1, 0), (1, 1)]


# --- R6: the cursor does not advance inside a bare `parallel` body --------------------------
# artiq_ir_generator.py:950-951 -- every top-level statement is preceded by at_mu(start_mu).


def test_R6_a_bare_delay_does_not_shift_later_statements():
    shim = ARTIQShim()
    with shim.parallel():
        shim.ttl_on(0)
        shim.delay_mu(80)
        shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 0)]      # both statements restart at the block's cursor
    assert shim.now_mu() == 80                  # ...but the block still takes 80 mu


def test_R6_a_bare_at_mu_does_not_shift_later_statements():
    shim = ARTIQShim()
    with shim.parallel():
        shim.ttl_on(0)
        shim.at_mu(800)
        shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 0)]
    assert shim.now_mu() == 800


def test_R6_now_mu_in_a_bare_parallel_body_is_the_block_start():
    shim = ARTIQShim()
    with shim.parallel():
        shim.delay_mu(80)
        assert shim.now_mu() == 0


# --- R7: how a block ends -------------------------------------------------------------------
# artiq_ir_generator.py:947-966 -- end_mu starts at start_mu and only ever grows.


def test_R7_the_cursor_after_a_block_is_its_longest_branch():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(8)
        with shim.sequential():
            shim.ttl_on(1)
            shim.delay_mu(80)

    assert shim.now_mu() == 80


def test_R7_an_event_after_a_block_is_stamped_after_it():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            shim.ttl_off(0)
    shim.ttl_on(5)

    assert trace(shim) == [(0, 0), (0, 10), (5, 10)]


def test_R7_a_block_never_ends_before_it_started():
    shim = ARTIQShim()
    shim.at_mu(1000)
    with shim.parallel():
        with shim.sequential():
            shim.at_mu(0)               # a branch that rewinds cannot drag the block back

    assert shim.now_mu() == 1000


# --- R8: block length depends on the cursor, never on emitted events ------------------------


def test_R8_event_timestamps_do_not_extend_a_block():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.at_mu(800)
            shim.ttl_on(0)              # stamped at 800...
            shim.at_mu(0)               # ...but the branch ends at 0

    assert trace(shim) == [(0, 100)]
    assert shim.now_mu() == 0


# --- R9: nesting ----------------------------------------------------------------------------


def test_R9_a_nested_block_starts_at_the_enclosing_branch_cursor():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
            with shim.parallel():
                shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_R9_a_nested_block_advances_the_enclosing_branch_cursor():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            with shim.parallel():
                with shim.sequential():
                    shim.ttl_on(0)
                    shim.delay_mu(80)
            shim.ttl_on(1)

    assert trace(shim) == [(0, 0), (1, 10)]


def test_R9_a_nested_block_does_not_disturb_a_later_sibling_branch():
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


def test_R9_a_nested_blocks_duration_reaches_the_outer_cursor():
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            with shim.parallel():
                with shim.sequential():
                    shim.ttl_on(0)
                    shim.delay_mu(80)

    assert shim.now_mu() == 80


# --- R10: empty blocks ----------------------------------------------------------------------


def test_R10_an_empty_block_leaves_the_cursor_alone():
    shim = ARTIQShim()
    shim.at_mu(1000)
    with shim.parallel():
        pass
    with shim.sequential():
        pass

    assert shim.now_mu() == 1000
    assert trace(shim) == []


# --- R11: exceptions ---------------------------------------------------------------------
# The compiler emits no at_mu() on the exceptional path, so the hardware cursor is wherever the
# raising statement left it. A trace from an aborted kernel has no meaningful "after", so the
# shim keeps what was submitted and unwinds cleanly.


def test_R11_an_exception_unwinds_the_context_stack():
    shim = ARTIQShim()
    with pytest.raises(RuntimeError, match="boom"):
        with shim.parallel():
            with shim.sequential():
                shim.ttl_on(0)
                raise RuntimeError("boom")

    shim.ttl_on(1)                                  # the shim is still usable, at top level
    assert [ch for ch, _ in trace(shim)] == [0, 1]  # already-submitted events are kept
    assert shim.now_mu() == 0                       # the aborted block contributed nothing


# --- R12: compound device ops open an implicit `sequential` ---------------------------------
# doc/manual/getting_started_core.rst:187-189 -- a top-level compound statement is "an atomic
# sequence executed within an implicit `with sequential`". `pulse_mu` is that shape.


def test_R12_pulse_mu_sequences_internally():
    shim = ARTIQShim()
    dev(shim, "ttl", 0).pulse_mu(80)

    assert trace(shim) == [(0, 0), (0, 10)]
    assert shim.now_mu() == 80


def test_R12_pulses_in_a_bare_parallel_body_start_together():
    # The manual's canonical example. Without the implicit sequential, both falling edges
    # would collapse onto the block start.
    shim = ARTIQShim()
    with shim.parallel():
        dev(shim, "ttl", 0).pulse_mu(1000)
        dev(shim, "ttl", 1).pulse_mu(500)

    assert trace(shim) == [(0, 0), (0, 125), (1, 0), (1, 62)]
    assert shim.now_mu() == 1000                     # the longest statement


# --- R13: the frame guard ----------------------------------------------------------------
# A `with`-based shim cannot see statement boundaries, so a helper that takes time directly in
# a bare parallel body would silently produce a trace ARTIQ would not. Raise instead.


def test_R13_a_helper_taking_time_in_a_bare_parallel_body_raises():
    def helper(shim):
        shim.delay_mu(80)

    shim = ARTIQShim()
    with pytest.raises(usage_error()):
        with shim.parallel():
            helper(shim)


def test_R13_the_same_helper_inside_a_sequential_branch_is_fine():
    def helper(shim):
        shim.ttl_on(0)
        shim.delay_mu(80)
        shim.ttl_off(0)

    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            helper(shim)

    assert trace(shim) == [(0, 0), (0, 10)]
    assert shim.now_mu() == 80


def test_R13_a_helper_that_only_emits_events_is_fine():
    # No time taken, so there is nothing to get wrong: an event lands at the block cursor
    # whether or not it came from a callee.
    def emit(shim, channel):
        shim.ttl_on(channel)

    shim = ARTIQShim()
    with shim.parallel():
        emit(shim, 0)
        emit(shim, 1)

    assert trace(shim) == [(0, 0), (1, 0)]


def test_R13_the_guard_does_not_fire_outside_a_bare_parallel_body():
    def helper(shim):
        shim.delay_mu(80)

    shim = ARTIQShim()
    helper(shim)
    with shim.sequential():
        helper(shim)

    assert shim.now_mu() == 160


# --- R14: divergence -------------------------------------------------------
# An inline loop/if in a bare parallel body is ONE top-level statement to ARTIQ (implicit
# sequential), but the shim sees only its individual calls, from the same frame, so the guard
# cannot catch it either. Documented, not fixed.


@pytest.mark.xfail(strict=True, reason="R14: inline compound statements are a known gap")
def test_R14_an_inline_loop_in_a_bare_parallel_body_diverges():
    shim = ARTIQShim()
    with shim.parallel():
        for ch in range(2):             # ARTIQ: one statement -> the two pulses sequence
            shim.ttl_on(ch)
            shim.delay_mu(80)

    assert trace(shim) == [(0, 0), (1, 10)]     # ARTIQ's answer; the shim gives [(0,0),(1,0)]


# --- R15: the API surface --------------------------------------------------------------------


def test_R15_device_objects_and_function_calls_agree():
    objects = ARTIQShim()
    with objects.parallel():
        with objects.sequential():
            dev(objects, "ttl", 0).on()
            objects.delay_mu(80)
            dev(objects, "ttl", 0).off()
        dev(objects, "dds", 4).set(frequency=1e6)

    functions = ARTIQShim()
    with functions.parallel():
        with functions.sequential():
            functions.ttl_on(0)
            functions.delay_mu(80)
            functions.ttl_off(0)
        functions.dds_set(4, frequency=1e6)

    assert full_trace(objects) == full_trace(functions)
    assert objects.now_mu() == functions.now_mu()


def test_R15_a_device_handle_is_stable():
    shim = ARTIQShim()
    assert dev(shim, "ttl", 0) is dev(shim, "ttl", 0)
    assert dev(shim, "ttl", 0) is not dev(shim, "ttl", 1)


def test_R15_one_channel_cannot_be_two_device_types():
    # Channel numbers are one flat RTIO namespace; ttl0 and dds0 would be the same hardware
    # channel, and the collision logic in sed_model would be nonsense.
    shim = ARTIQShim()
    dev(shim, "ttl", 0)
    with pytest.raises(usage_error()):
        dev(shim, "dds", 0)

    other = ARTIQShim()
    other.ttl_on(0)
    with pytest.raises(usage_error()):
        other.dds_set(0, frequency=1e6)


def test_R15_verify_runs_the_oracle_over_the_captured_trace():
    from sed_model import Outcome

    shim = ARTIQShim()
    for i in range(9):
        shim.ttl_on(i)

    res = shim.verify(lanes=8)
    assert not res.ok
    assert [(s.index, s.outcome) for s in res.errors] == [(8, Outcome.SEQUENCE_ERROR)]


# --- R16: hygiene -----------------------------------------------------------------------------


def test_R16_two_shims_do_not_share_state():
    a, b = ARTIQShim(), ARTIQShim()
    a.delay_mu(80)
    a.ttl_on(0)

    assert b.now_mu() == 0
    assert trace(b) == []


def test_R16_running_a_kernel_prints_nothing(capsys):
    shim = ARTIQShim()
    with shim.parallel():
        with shim.sequential():
            shim.ttl_on(0)
            shim.delay_mu(80)
    shim.get_events()

    assert capsys.readouterr().out == ""


def test_R16_the_same_kernel_twice_gives_the_same_trace():
    def kernel(shim):
        with shim.parallel():
            with shim.sequential():
                shim.ttl_on(0)
                shim.delay_mu(80)
                shim.ttl_off(0)
            shim.dds_set(4, frequency=1e6)

    first, second = ARTIQShim(), ARTIQShim()
    kernel(first)
    kernel(second)

    assert full_trace(first) == full_trace(second)
    assert first.now_mu() == second.now_mu()
