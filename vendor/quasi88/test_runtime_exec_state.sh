#!/bin/bash
#
# test_runtime_exec_state.sh - Runtime State Test Launcher
#
# Purpose: Run runtime execution state tests in headless mode
#
# PRECONDITIONS:
#   - QUASI88 built with SDL2
#   - ROM files available in runtime/roms/
#   - SDL dummy drivers available
#
# USAGE:
#   ./vendor/quasi88/test_runtime_exec_state.sh
#
# OUTPUT:
#   - Test results on stdout
#   - Exit code: 0 = all tests pass, 1 = tests failed
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Runtime Execution State Tests"
echo "=========================================="
echo ""

# Ensure headless SDL environment
export SDL_VIDEODRIVER=dummy
export SDL_AUDIODRIVER=dummy

echo "[1/2] Building runtime state test binary"
make -f Makefile.test clean test_runtime_exec_state 2>&1 || {
    echo "[ERROR] Build failed"
    exit 1
}

echo ""
echo "[2/2] Running runtime state test binary"
echo "(headless mode with SDL dummy drivers)"
echo ""

./test_runtime_exec_state
EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "Runtime State Tests: PASSED"
else
    echo "Runtime State Tests: FAILED"
fi
echo "=========================================="

exit $EXIT_CODE
