"""A model of ARTIQ's timeline semantics, over kernels-as-data.

`evaluate()` is a direct transcription of what ARTIQ does; `run_on_shim()`
drives the shim's API with the same kernel. If the two disagree, the shim is
wrong (`test_reference_fidelity.py` performs differential testing of this model
against m-labs' own host-side time manager).

Sources:

  artiq/compiler/transforms/artiq_ir_generator.py:946-967  `with parallel:`
      start_mu = now_mu(); end_mu = start_mu
      for each TOP-LEVEL statement:  at_mu(start_mu); <stmt>; end_mu = max(now_mu(), end_mu)
      at_mu(end_mu)

  artiq/compiler/transforms/artiq_ir_generator.py:925-927  `with sequential:`
      self.visit(node.body)          # a no-op at any nesting depth

  artiq/sim/time.py
      In a parallel context the cursor does not move; each take_time() maxes a block_duration.

  doc/manual/getting_started_core.rst:158, 187-189
      The same rules in prose, including "top-level statements" and the implicit
      `with sequential` around a compound statement (which is what `Pulse` models here).

This is does not replace the shim. Branch structure is explicit in the tree, so the rules can be
read straight off the recursion without the cursor stack, context managers, buffers, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

COARSE_SHIFT = 3          # coarse = mu >> 3, matching l3_gateware.FINE_TS_WIDTH


# --- the kernel IR
# One node per ARTIQ top-level statement. `Seq`/`Par` bodies are lists of statements.


@dataclass
class Out:
    """An RTIO output submission at the current cursor. Takes no time."""
    channel: int
    replace: bool = False


@dataclass
class Delay:
    """delay_mu(mu). May be negative."""
    mu: int


@dataclass
class At:
    """at_mu(mu). May move the cursor backwards."""
    mu: int


@dataclass
class Pulse:
    """A compound device op (`ttl.pulse_mu`): One statement that sequences internally.

    This is the manual's canonical `with parallel:` example, and the reason the shim needs an
    implicit `sequential` context around compound device methods.
    """
    channel: int
    mu: int


@dataclass
class Seq:
    """`with sequential:` children run one after another."""
    body: List[object] = field(default_factory=list)


@dataclass
class Par:
    """`with parallel:` every child starts at the block's entry cursor."""
    body: List[object] = field(default_factory=list)


def desugar(node):
    """Compound ops -> their explicit sequential form."""
    if isinstance(node, Pulse):
        return Seq([Out(node.channel), Delay(node.mu), Out(node.channel)])
    return node


# --- the model


def evaluate(node, t: int, out: List[Tuple[int, int, bool]]) -> int:
    """Return the cursor after `node`, appending (channel, mu, replace) to `out`.

    `out` grows in submission order (i.e., program order).
    """
    node = desugar(node)

    if isinstance(node, Out):
        out.append((node.channel, t, node.replace))
        return t
    if isinstance(node, Delay):
        return t + node.mu
    if isinstance(node, At):
        return node.mu
    if isinstance(node, Seq):
        for child in node.body:
            t = evaluate(child, t, out)
        return t
    if isinstance(node, Par):
        end = t                                     # end_mu = start_mu
        for child in node.body:
            end = max(end, evaluate(child, t, out))  # every child starts at t
        return end
    raise TypeError(f"not a kernel node: {node!r}")


def expected(node, start: int = 0, shift: int = COARSE_SHIFT):
    """(trace, end_cursor) for `node`, where trace is [(channel, coarse_ts, replace), ...]."""
    fine: List[Tuple[int, int, bool]] = []
    end = evaluate(node, start, fine)
    return [(ch, mu >> shift, rep) for ch, mu, rep in fine], end


# --- driving a shim with the same kernel


def run_on_shim(node, shim, device_objects: bool = True) -> None:
    """Execute `node` through the shim's ARTIQ-shaped API.

    `device_objects` picks the surface: ttl(ch).on() / dds(ch).set() when True, the
    ttl_on(ch) / dds_set(ch) functions when False. Both must produce the same trace.
    """
    if isinstance(node, Out):
        if node.replace:
            shim.dds(node.channel).set(frequency=1e6) if device_objects \
                else shim.dds_set(node.channel, frequency=1e6)
        else:
            shim.ttl(node.channel).on() if device_objects else shim.ttl_on(node.channel)
    elif isinstance(node, Delay):
        shim.delay_mu(node.mu)
    elif isinstance(node, At):
        shim.at_mu(node.mu)
    elif isinstance(node, Pulse):
        if device_objects:
            shim.ttl(node.channel).pulse_mu(node.mu)
        else:
            shim.ttl_pulse_mu(node.channel, node.mu)
    elif isinstance(node, Seq):
        with shim.sequential():
            for child in node.body:
                run_on_shim(child, shim, device_objects)
    elif isinstance(node, Par):
        with shim.parallel():
            for child in node.body:
                # Time-taking leaves are submitted from this frame on purpose: to ARTIQ they
                # are top-level statements of the block, and the only way to say that in
                # Python is to write the call in the frame that owns the `with`. Recursing
                # would make them a helper's calls, which the frame guard rejects 
                # because ARTIQ would have sequenced them.
                if isinstance(child, Delay):
                    shim.delay_mu(child.mu)
                elif isinstance(child, At):
                    shim.at_mu(child.mu)
                else:
                    run_on_shim(child, shim, device_objects)
    else:
        raise TypeError(f"not a kernel node: {node!r}")


# --- readable failures


def to_source(node, indent: int = 0, device_objects: bool = True) -> str:
    """Render `node` as the kernel source it stands for, so a fuzz failure is copy-pasteable."""
    pad = "    " * indent
    if isinstance(node, Out):
        call = (f"shim.dds({node.channel}).set(frequency=1e6)" if node.replace
                else f"shim.ttl({node.channel}).on()")
        if not device_objects:
            call = (f"shim.dds_set({node.channel}, frequency=1e6)" if node.replace
                    else f"shim.ttl_on({node.channel})")
        return pad + call
    if isinstance(node, Delay):
        return f"{pad}shim.delay_mu({node.mu})"
    if isinstance(node, At):
        return f"{pad}shim.at_mu({node.mu})"
    if isinstance(node, Pulse):
        return f"{pad}shim.ttl({node.channel}).pulse_mu({node.mu})"
    if isinstance(node, (Seq, Par)):
        head = "sequential" if isinstance(node, Seq) else "parallel"
        lines = [f"{pad}with shim.{head}():"]
        if not node.body:
            lines.append(f"{pad}    pass")
        for child in node.body:
            lines.append(to_source(child, indent + 1, device_objects))
        return "\n".join(lines)
    raise TypeError(f"not a kernel node: {node!r}")
