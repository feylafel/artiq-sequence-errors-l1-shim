# Running the oracle

`oracle/` is the project root for all code in this directory (L1 shim, L2
model, L3 gateware check). Note: It is not a Python package, there is no
`oracle/__init__.py`.

## Run everything from `oracle/`

```
cd oracle
```

`pyproject.toml` lives here, and its pytest config is written relative to this
directory:

`pytest` picks its rootdir by walking up from where you invoke it until it
finds that config, so invoking from anywhere else changes which paths resolve.

## Commands

```
pytest                                      # full suite
python3 sed_model.py                        # L2 demo
python3 examples/sequence_error_kernel.py   # the hand-written BAD / FIXED traces
python3 examples/run.py                     # example kernels through the L1 shim
```

## The Layer 1 contract

`notes/timeline-rules.md` states the shim's timeline semantics as numbered
rules, each cited to ARTIQ's own source. Four relevant files:

* `tests/test_l1_acceptance.py`: one test per rule
* `tests/reference_timeline.py`: an independent model of the semantics
* `tests/test_l1_fuzz.py`: random nested kernels through both
* `tests/test_reference_fidelity.py`: checks the reference itself against m-labs' `artiq/sim/time.py`


```
pytest tests/test_l1_fuzz.py --fuzz-kernels 20000   # deeper sample
pytest tests/test_l1_fuzz.py --fuzz-seed 7          # a different seed
```

The reference-fidelity cross-check skips unless an ARTIQ source tree is
present; `setup_l3.sh` below provides one.

```
bash scripts/setup_l3.sh
. .venv/bin/activate && pytest
```

Running `pytest` with an explicit path from the repo root also works, because
pytest still finds `oracle/pyproject.toml` and sets rootdir to `oracle/`:

```
pytest oracle/tests                     # ok
```


## Import convention

Modules here import each other top-level, with no package prefix:

```python
from sed_model import Event, SEDModel     # correct
from L1_shim import ARTIQShim             # correct
from examples.kernels import simple_pulse # correct (examples/ is a package)
```

