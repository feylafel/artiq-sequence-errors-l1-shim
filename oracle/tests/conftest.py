"""Shared pytest options for the oracle suite."""

import pytest


def pytest_addoption(parser):
    parser.addoption("--fuzz-kernels", type=int, default=500,
                     help="random kernels per fuzz test (default 500)")
    parser.addoption("--fuzz-seed", type=int, default=0,
                     help="RNG seed for the fuzz tests (default 0, i.e. reproducible)")


@pytest.fixture
def fuzz_kernels(request):
    return request.config.getoption("--fuzz-kernels")


@pytest.fixture
def fuzz_seed(request):
    return request.config.getoption("--fuzz-seed")
