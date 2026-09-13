# Functional simulators for ARTIQ RTIO

## The existing simulator (DAX) and why it "lacks RTIO"

The simulator referenced in the notes (IEEE 9951197 = arXiv:2210.14364,
Riesebos et al., "Functional Simulation of Real-Time Quantum Control Software")
is the **DAX simulator**, part of the *Duke ARTIQ Extensions* (DAX) library.
Its design is the reason it can't see sequence errors:

- It uses API simulation. Devices are simulated at the driver-API level against a
  simplified device model. When you call `ttl.pulse()` or `dds.set()`, a simulation driver
  records the effect — but there is deliberately no execution model of the hardware
  gateware. That abstraction is what makes it ~7x faster than on-core execution.
- Sequence/collision/busy errors are **not** properties of the driver API. They are emergent
  properties of the RTIO gateware — specifically the Scalable Event Dispatcher (SED) that
  packs events into FIFO lanes. Because DAX models above that layer, it structurally
  cannot observe them. This is a layer boundary, not a missing/configurable feature.

So: DAX gives correct device-state/timing semantics, but no RTIO lane behavior.

## ARTIQ's real RTIO logic is already simulation-capable

We may not need to invent the error model from scratch. The ground truth exists
as open source. ARTIQ's SED is written in **Migen** (Python-subset HDL), and
Migen modules run in a cycle-accurate Python simulator (`run_simulation`).
Relevant file: `artiq/gateware/rtio/sed/lane_distributor.py` (m-labs/artiq).

The lane-distribution algorithm is small and fully specified.

State: `current_lane`, and `last_coarse_timestamp` per lane (8 lanes by default).

For each output event, in submission order, with coarse timestamp `t` on a channel:

1. If `t >` the last timestamp written to the current lane -> stay in the current lane.
2. Else -> advance to the next lane (`(current_lane + 1) mod N`), wrapping around. (Also
   forced when the current lane is nearly full / when "spreading" is enabled.)
3. After selecting the lane, if `t <=` that lane's last timestamp -> **sequence error**.
   Otherwise write and update the lane's last timestamp.

Key consequence for the project: a sequence error depends only on (1) the
submission order and (2) coarse timestamps of events relative to the
lane-assignment state machine—not on the actual physics. That is what makes
static detection plausible, and it's a self-contained model.

Related errors live at different layers:

- **Collision error** — two events on the same channel with the same coarse timestamp (and
  no replacement). Treat as a user logic bug -> report, don't repair.
- **Busy error** — a channel still executing a previous event (single events longer than one
  coarse cycle). Largely a device-timing property.

## End-to-end pipeline: kernel -> trace -> oracle

The idea: take a real ARTIQ kernel, run it through a DAX-style API simulation
to get an event trace, then run that trace through L2/L3.

### The pipeline

```
real ARTIQ kernel
      |  run it (DAX-style API sim)         <- Layer 1: trace capture
      v
event trace:  [(channel, coarse_ts), ...] in submission order
      |
      +--> L2 (sed_model.py)      -> verdict: ok / sequence_error / underflow + lanes
      +--> L3 (real gateware)     -> same verdict
```

Two distinct things are happening:

1. DAX is the front-end (Layer 1), not part of the validation. It supplies the input,
   the ordered event stream. It deliberately does not produce an error verdict (that's its
   documented gap). DAX feeds traces, the oracle judges them.
2. The validation is L2-vs-L3, which we already did and which is independent of DAX. DAX
   just lets us exercise that oracle on realistic traces from actual kernels instead of
   hand-written ones.

### Submission order vs timestamps

A sequence error depends on the order events are submitted (program order), not
their timestamps. DAX executes the kernel in program order and you capture
`(channel, now_mu)` at the moment of each RTIO output call, so submission order
falls out naturally. But it means the trace must be a faithfully ordered log of
output submissions, not a set of events you sort by timestamp afterward. (Then
convert `mu -> coarse` with `coarse = mu >> 3` to feed L2/L3.) The example
traces already encode this, `EVENT_TRACE` is in submission order.

### Note: this is the *dynamic* path

DAX runs *one execution*: one set of inputs, one path through every
data-dependent branch/loop. A sequence error hiding on an untaken branch won't
appear in that trace. That's fine, because in this architecture the DAX+oracle
pipeline is a ground-truth labeler: a way to (a) confirm the error model on
real kernels and (b) generate labeled examples to test the static analysis
against. The static analysis is the thing that predicts errors across all paths
without running the kernel.

Practical note: full DAX/ARTIQ is a heavy install. If all we want from Layer 1
is the trace, a short kernel-API shim that records `(channel, now_mu)` on each
output call gets the same thing without the dependency. We could use DAX only
if we also want its device-semantics modeling, but it's not clear if that's
necessary at this point.

## Sources

- DAX functional simulation paper (arXiv:2210.14364): <https://arxiv.org/pdf/2210.14364>
- ARTIQ RTIO concepts & error definitions: <https://m-labs.hk/artiq/manual/rtio.html>
- ARTIQ SED lane_distributor.py:
  <https://github.com/m-labs/artiq/blob/master/artiq/gateware/rtio/sed/lane_distributor.py>
- RTIO scalable event dispatcher design (m-labs/artiq#778):
  <https://github.com/m-labs/artiq/issues/778>
