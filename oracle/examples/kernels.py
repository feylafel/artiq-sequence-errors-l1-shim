"""Collection of example kernels."""

def simple_pulse(shim):
    shim.ttl_on(0)
    shim.delay_mu(100)
    shim.ttl_off(0)

def parallel_burst(shim):
    with shim.parallel():
        for i in range(4):
            shim.ttl_on(i)

def sequence_error(shim):
    with shim.parallel():
        for i in range(9):
            shim.ttl_on(i)
