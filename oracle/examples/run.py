"""Run kernels against the shim."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from L1_shim import ARTIQShim
from examples.kernels import simple_pulse, parallel_burst, sequence_error


def run(kernel_func):
    shim = ARTIQShim()
    kernel_func(shim)
    print(shim.verify().summary())


if __name__ == "__main__":
    print("Simple Pulse:")
    run(simple_pulse)

    print("\nParallel Burst:")
    run(parallel_burst)

    print("\nSequence Error:")
    run(sequence_error)
