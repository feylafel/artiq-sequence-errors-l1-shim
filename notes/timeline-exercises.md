# Timeline exercises

Goal: build intuition for how ARTIQ's timeline produces an event stream, and
when that stream trips a **sequence error**. For each kernel below:

1. Write the **submission-order** list of `(channel, coarse_ts)` events.
2. Predict the **verdict**: `OK`, or `sequence error` (and on which event index, 0-based).
3. Then check with the model (command below).

**Rules**:

- There are **8 lanes**, numbered 0–7. Within a lane, coarse timestamps must be **strictly
  increasing**.
- The hardware keeps a `current_lane` cursor. For each event: if its coarse timestamp is
  strictly greater than the last timestamp written to the current lane, it stays; otherwise
  it advances to the next lane (`+1`, wrapping `7 → 0`). If the lane it lands on already holds a
  timestamp `>=` the new one → sequence error (the event is dropped, no exception).
- **Coarse vs fine:** `coarse_ts = fine_mu >> 3`, i.e., **8 fine machine units (mu) per coarse
  cycle**. Two events less than 8 mu apart have the same coarse timestamp.
- Events are submitted in **program order**, each stamped with the timeline cursor at the moment
  of the call. Assume `now_mu()` starts at `0`. All channels below are distinct TTLs unless
  noted.

**How to check**:

```python
from sed_model import Event, SEDModel
trace = [(0, 0), (1, 1)]                       # your (channel, coarse_ts) list
res = SEDModel(lanes=8).run([Event(channel=c, coarse_ts=t) for c, t in trace])
print(res.summary())
```

Translate each `delay_mu(n)` into a coarse step of `n // 8` (and remember a delay `< 8` may not
change the coarse timestamp at all). Fill in the trace, predict, then run.

---

## Exercise 1 — straight line, spread out (worked example)

```python
for i in range(4):
    ttl[i].on()
    delay_mu(8)        # advance one full coarse cycle
```

**Worked solution — this one is done for you; use it as the template for the rest.**

*Step 1 — build the submission-order trace.* The cursor starts at `now_mu() =
0` fine mu. Each `ttl[i].on()` stamps an event with the cursor at that moment;
each `delay_mu(8)` then advances the cursor 8 fine mu. Convert to coarse with
`coarse_ts = fine_mu >> 3` (i.e., `// 8`):

| iter `i` | cursor at `.on()` (fine mu) | event | `coarse_ts = fine >> 3` | cursor after `delay_mu(8)` |
|---|---|---|---|---|
| 0 | 0  | `ttl[0].on()` | `0 >> 3 = 0`  | 8  |
| 1 | 8  | `ttl[1].on()` | `8 >> 3 = 1`  | 16 |
| 2 | 16 | `ttl[2].on()` | `16 >> 3 = 2` | 24 |
| 3 | 24 | `ttl[3].on()` | `24 >> 3 = 3` | 32 |

*Step 2 — apply the lane logic.* Start with `current_lane = 0`, all lanes
empty. An event stays in the current lane if its `coarse_ts` is strictly
greater than that lane's last-written timestamp; otherwise it advances a lane
(and errors if the new lane already holds a `>=` ts).

| event | current lane | lane's last ts | `ts > last`? | action | lane 0 after |
|---|---|---|---|---|---|
| `(0,0)` | 0 | — (empty) | yes (empty) | stay, write 0 | `[0]`       |
| `(1,1)` | 0 | 0 | `1 > 0` ✓ | stay, write 1 | `[0,1]`     |
| `(2,2)` | 0 | 1 | `2 > 1` ✓ | stay, write 2 | `[0,1,2]`   |
| `(3,3)` | 0 | 2 | `3 > 2` ✓ | stay, write 3 | `[0,1,2,3]` |

Every timestamp is strictly greater than the previous one written to lane 0, so
the cursor never advances — all four stack into one lane.

Trace: `(0,0) (1,1) (2,2) (3,3)`  Verdict: **OK**  (lanes `0,0,0,0`)

Check it:

```python
from sed_model import Event, SEDModel
trace = [(0, 0), (1, 1), (2, 2), (3, 3)]
print(SEDModel(lanes=8).run([Event(channel=c, coarse_ts=t) for c, t in trace]).summary())
```

## Exercise 2 — eight at once

```python
t = now_mu()
for i in range(8):
    at_mu(t)           # every event at the same cursor value
    ttl[i].on()
```

Trace: `__________________`  Verdict: `______`

## Exercise 3 — nine at once

```python
t = now_mu()
for i in range(9):
    at_mu(t)
    ttl[i].on()
```

Trace: `__________________`  Verdict: `______`

## Exercise 4 — tiny (sub-coarse) delays

```python
t = now_mu()
for i in range(9):
    at_mu(t + (i % 4))   # fine offsets 0,1,2,3,0,1,2,3,0 — all under 8 mu
    ttl[i].on()
```

Trace: `__________________`  Verdict: `______`

(Hint: what is `(t + 3) >> 3` when `t = 0`? Do the fine offsets change the coarse timestamp?)

## Exercise 5 — the fix

```python
t = now_mu()
for i in range(9):
    at_mu(t + i*8)       # one full coarse cycle apart
    ttl[i].on()
```

Trace: `__________________`  Verdict: `______`

## Exercise 6 — counting down

```python
t = now_mu()
for i in range(9):
    at_mu(t + (8 - i)*8)   # coarse timestamps 8, 7, 6, … , 0
    ttl[i].on()
```

Trace: `__________________`  Verdict: `______`

(Hint: the lane rule is "strictly increasing", not "all distinct". Does descending help?)

## Exercise 7 — concurrency

```python
with parallel:
    with sequential:           # branch A
        ttl[0].on()            # at cursor t
        delay_mu(8)
        ttl[0].off()           # at cursor t + 8
    with sequential:           # branch B
        ttl[1].on()            # also starts at t (parallel resets the cursor)
        delay_mu(8)
        ttl[1].off()           # at t + 8
```

`parallel` runs each branch from the same starting cursor, but the branches are
still submitted in source order (A fully, then B). Note `ttl[0]` appears twice
(on, then off) — that's the same channel at two different coarse timestamps,
which is fine.

Trace: `__________________`  Verdict: `______`

---

## Answer key

Verified against `SEDModel(lanes=8)`. Lane assignments are shown so you can
check your reasoning, not just the verdict.

| Ex | Trace `(channel, coarse_ts)` (submission order) | Lanes | Verdict |
|----|--------------------------------------------------|-------|---------|
| 1 | `(0,0) (1,1) (2,2) (3,3)` | `0,0,0,0` | **OK** — each strictly later, all stack in lane 0 |
| 2 | `(0,0)…(7,0)` (8 events, all coarse 0) | `0,1,2,3,4,5,6,7` | **OK** — exactly fills the 8 lanes |
| 3 | `(0,0)…(8,0)` (9 events, all coarse 0) | `0,1,2,3,4,5,6,7,—` | **sequence error on event 8** — wraps to lane 0, which already holds coarse 0 |
| 4 | `(0,0)…(8,0)` — fine offsets `% 4` all map to coarse 0 | `0,1,2,3,4,5,6,7,—` | **sequence error on event 8** — sub-coarse delays don't separate events; identical to Ex 3 |
| 5 | `(0,0) (1,1) (2,2) … (8,8)` | `0,0,0,0,0,0,0,0,0` | **OK** — one coarse cycle apart is exactly the repair |
| 6 | `(0,8) (1,7) (2,6) … (8,0)` | `0,1,2,3,4,5,6,7,—` | **sequence error on event 8** — descending exhausts lanes just like equal does |
| 7 | `(0,0) (0,1) (1,0) (1,1)` | `0,0,1,1` | **OK** — A: on@0→lane0, off@1→lane0; B: on@0 can't follow coarse 1 so switches to lane1, off@1→lane1 |

### What each exercise teaches

- **1 vs 2 vs 3:** the capacity is the *number of lanes* (8). The 9th equal-timestamp event is
  the first that must reuse a lane it can't.
- **4:** "delay" is not enough — only **coarse-cycle** separation counts. Fine (sub-8-mu) offsets
  are invisible to the lane logic. This is the single most common real-world surprise.
- **5:** the canonical repair: spread events by ≥ 1 coarse cycle and the same choreography becomes safe.
- **6:** the invariant is *strictly increasing within a lane*, so **descending** timestamps are
  just as bad as equal ones — they force a lane switch every event.
- **7:** `with parallel:` makes the **submission order** interleave timestamps (`0,1,0,1`) even
  though each branch is individually monotone. Submission order ≠ timestamp order.
