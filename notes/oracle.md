# Overview — sequence-error detection & oracle for ARTIQ

The goal of this project is to build a static analysis that finds (and
eventually repairs) RTIO sequence errors in ARTIQ kernels—a class of bug that
fails silently on real control systems for quantum physics and quantum
information systems.

## The 60-second picture

ARTIQ programs ("kernels") schedule hardware events on a timeline with
nanosecond resolution. The hardware packs those events into 8 FIFO lanes;
within a lane, timestamps must be strictly increasing. If too many
equal-or-decreasing-timestamp events pile up, the hardware can't place one—a
**sequence error**—and, crucially, no exception is raised. The program keeps
running. We want to catch this at compile time instead.

We already have an oracle that decides, for a given event stream, whether a
sequence error occurs:

- `sed_model.py` — **L2**: a small Python model of the hardware's lane logic.
    - SED stands for scalable event dispatcher, the RTIO gateware component in
      ARTIQ that takes the stream of output events and packs them into the FIFO
      lanes.
- `l3_gateware.py` — **L3**: drives ARTIQ's gateware and agrees with L2
  (validated by tests). L3 is ground truth; L2 is the human-readable version.

The whole pipeline at a glance: a kernel becomes a trace, and two oracles judge it:

```
   ARTIQ kernel  (Python source)
          │
          │   L1: Trace capture.
          │   Run the kernel; for every RTIO output call, record
          │   (channel, now_mu) in submission order.
          │
          ▼     convert:  coarse_ts = now_mu >> 3
   ┌─ EVENT TRACE ─────────────────────────────────────────────┐
   │   [(ch, coarse_ts), (ch, coarse_ts), …]                   │
   │   kept in submission order (program order ≠ time order)   │
   └───────────────────────────────────────────────────────────┘
          │
          ├────────────────┐
          ▼                ▼
   ┌─────────────┐  ┌─────────────┐
   │  L2 oracle  │  │  L3 oracle  │
   │  sed_model  │  │ l3_gateware │
   └──────┬──────┘  └──────┬──────┘
          │                │
   small readable     drives ARTIQ's
   lane model         migen gateware
          │           (ground truth)
          │                │
          │                │
          ▼                ▼
       verdict  <=====>  verdict
          │
          │   pinned by  tests/test_l3_fidelity.py   (asserts L2 == L3)
          ▼
   verdict ∈ { ok | sequence_error | underflow }   +  lane per event
```

Notes:

- L1 needs to be implemented.
- L2 is the oracle we will use for sequence validation for specific kernels.
- L3 exists only to prove L2 is faithful; hardware simulation will be too slow to use in practice.

## Verification of timeline and lane semantics

The timeline and lane semantics have different "ground truths" in the ARTIQ compiler.
The timeline semantics come from `artiq/sim/time.py`.
The lane semantics from the SED model gateware (migen).

```
                 L1: timeline semantics          L2: lane semantics
                 ----------------------          ------------------

                 L1_shim.py                      sed_model.py
                 (ARTIQ shim API)                (small, abstracted lane model)
                      │                               │
                      │                               │
   tests  =>     test_l1_acceptance.py           test_l3_fidelity.py
                 test_l1_fuzz.py                      │
                      │                               │
                      ▼                               │
reference =>     tests/reference_timeline.py          │        
                      │                               │
                      │                               │
   tests  =>     test_reference_fidelity.py           │                 
                      ▼                               ▼                
ground truth     artiq/sim/time.py               l3_gateware.py
                 artiq_ir_generator.py           (drives ARTIQ's own migen SED)
```

