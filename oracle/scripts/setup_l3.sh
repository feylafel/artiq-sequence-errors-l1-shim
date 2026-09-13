#!/usr/bin/env bash
# Set up the environment for the Layer 3 gateware fidelity check.
#
# ARTIQ and its deps (misoc, sipyco) are not on PyPI — they are distributed via git/nix.
# But the gateware modules (LaneDistributor) are pure Python and only need migen + misoc +
# the ARTIQ source on sys.path. We avoid a full ARTIQ install (no nac3/LLVM toolchain needed).
#
# Run from the repo root:  bash scripts/setup_l3.sh
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --upgrade pip

# Pure-PyPI deps
pip install pytest numpy migen

# git-only m-labs deps (gateware needs these; not on PyPI)
pip install "git+https://github.com/m-labs/misoc.git"
pip install "git+https://github.com/m-labs/sipyco.git"

# ARTIQ source tree on the path (gateware Python only; no full install)
if [ ! -d .artiq-src ]; then
    git clone --depth 1 https://github.com/m-labs/artiq.git .artiq-src
fi

echo
echo "Done. Run the suite with:  . .venv/bin/activate && pytest"
echo "L3 tests skip automatically if this setup is absent."
