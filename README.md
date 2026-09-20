## Running the tests

From the `oracle/` directory:

    python -m pytest tests/test_l1_acceptance.py tests/test_l1_fuzz.py tests/test_l1_shim.py

Verbose output with a skip summary:

    python -m pytest -v -rs

Full suite (includes L2 oracle and reference fidelity tests):

    python -m pytest
