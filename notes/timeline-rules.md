# Timeline semantics rules

## The cursor

**R1** — `now_mu()` returns the timeline cursor in mu, starting at 0.
`delay_mu(d)` advances it by `d`; `at_mu(t)` sets it to `t`. Both accept
backward motion (`d < 0`, `t < now_mu()`); ARTIQ allows it and the shim must
not clamp or reject it.

**R2** — Every output call appends `(channel, cursor, replace)` to a single
ordered list, in program order, regardless of block nesting. The trace is never
sorted. (A sequence error is a property of submission order; sorting by
timestamp would erase the thing we are measuring.)

**R3** — `mu -> coarse` conversion happens exactly once, on the way out:
`coarse = mu >> 3`, an arithmetic shift, so it floors for negative timestamps.
Events recorded inside a block convert the same way as events recorded at top
level.

**R4** — `replace` is a property of the device class: TTL outputs are `False`
(they collide), DDS writes are `True` (they overwrite). `sed_model` uses it for
collision detection only.

## Blocks

**R5** — `with sequential:` is a no-op at any depth
(`artiq_ir_generator.py:925-927` is a bare `visit(body)`). It has no effect of
its own; it matters only because *one* `with sequential:` block is *one*
top-level statement of an enclosing `parallel`, and so delimits a branch.

**R6** — Inside a `parallel` block the cursor does not advance. Every top-level
statement of the body starts at the block's entry cursor, because the compiler
emits `at_mu(start_mu)` before each one. Therefore in a bare parallel body,
`delay_mu` does not shift the statements after it, and `now_mu()` reads the
block start.

```python
with parallel:
    ttl_on(0)         # at start
    delay_mu(80)      # its own statement: lengthens the block, shifts nothing
    ttl_on(1)         # at start, not start+80
```

**R7** — A block ends at `max(block start, end cursor of each branch)`.
`end_mu` is initialized to `start_mu`, so a branch that rewinds cannot drag the
block end backwards. On exit the cursor is that end.

**R8** — Block length is a function of the cursor only. Emitted event
timestamps never extend a block: a branch that stamps an event at `start+800`
and then rewinds to `start` ends at `start`.

**R9** — Nesting composes: a block starts at the enclosing context's cursor and
contributes its end to it. A `sequential` parent takes the child's end as its
new cursor; a `parallel` parent maxes it into `end_mu`. Sibling branches are
unaffected — each starts at the block's entry cursor.

**R10** — An empty block records nothing and leaves the cursor where it was.

**R11** — If an exception escapes a block, the context stack unwinds, events
already submitted are kept, and the block contributes nothing to the enclosing
cursor. (The compiler emits no `at_mu` on the exceptional path, so the hardware
cursor is wherever the raising statement left it; a trace from an aborted
kernel has no meaningful "after", so we define one.)

## Compound statements

**R12** — A device method that spans time (`ttl.pulse_mu`) opens an implicit
`sequential` context. The manual (`getting_started_core.rst:187-189`) says a
top-level compound statement is "an atomic sequence executed within an implicit
`with sequential`", and this is the manual's own canonical example:

```python
with parallel:
    ttl0.pulse_mu(1000)     # both rising edges at the block start,
    ttl1.pulse_mu(500)      # each falling edge after its own duration
```

**R13** — Taking time from a helper frame directly inside a bare `parallel`
body raises `ShimUsageError`. ARTIQ would run that call as one top-level
statement (implicit `sequential`); a context manager cannot see statement
boundaries, so the shim would silently produce a trace the hardware would not.
The error names the fix: wrap the branch in `with sequential:`.

Emitting *events* from a helper is fine. An event lands at the block cursor
either way. Only time-taking is ambiguous.

**R14** — Known gap, accepted: an inline `for`/`if`/`while` written directly in
a bare parallel body is one statement to ARTIQ but a series of same-frame calls
to the shim, so R13 cannot catch it either. Documented by an
`xfail(strict=True)` test. Wrap such branches in `with sequential:`. Closing
this would take statement attribution (mapping calls to source statements via
the caller's frame line numbers, the way the compiler works over the AST) — out
of scope here.

## Surface

**R15** — Both API surfaces exist and agree: device objects
`shim.ttl(ch).on()/.off()/ .pulse_mu(d)`, `shim.dds(ch).set(**kw)`, and the
function forms `ttl_on`, `ttl_off`, `ttl_pulse_mu`, `dds_set`. A device handle
is stable (`shim.ttl(0) is shim.ttl(0)`). Channel numbers are one flat RTIO
namespace, so a channel used as two device types raises `ShimUsageError`.
`get_events()` returns `sed_model.Event`s in submission order;
`verify(**kwargs)` runs `SEDModel(**kwargs)` over them.

**R16** — No module-level state, no debug output, deterministic: two shims are
independent, and the same kernel run twice gives the same trace.

## Out of scope

Underflow and slack (no wall-clock model at L1 — `sed_model` keeps its
`minimum_coarse_timestamp` knob), RTIO inputs (`gate_rising`, `timestamp_mu`),
the seconds-based API (`delay`, `us`/`ns` units — this project is mu and coarse
throughout), DMA, and `interleave`.

