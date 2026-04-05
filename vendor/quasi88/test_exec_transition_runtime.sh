#!/bin/bash
#
# test_exec_transition_runtime.sh - Run Mode Transition Runtime Proof Tests
#
# Purpose: Run mode transition runtime tests in headless mode
#
# PRECONDITIONS:
#   - QUASI88 built with SDL2
#   - ROM files available in runtime/roms/
#   - SDL dummy drivers available
#
# USAGE:
#   ./vendor/quasi88/test_exec_transition_runtime.sh
#
# OUTPUT:
#   - Test results on stdout
#   - Exit code: 0 = all tests pass, 1 = tests failed
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Mode Transition Runtime Tests"
echo "=========================================="
echo ""

# Ensure headless SDL environment
export SDL_VIDEODRIVER=dummy
export SDL_AUDIODRIVER=dummy

echo "[1/2] Building mode transition runtime test binary"
make -f Makefile.test clean test_exec_transition_runtime 2>&1 || {
    echo "[ERROR] Build failed"
    exit 1
}

echo ""
echo "[2/2] Running mode transition runtime test binary"
echo "(headless mode with SDL dummy drivers)"
echo ""

./test_exec_transition_runtime
EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "Mode Transition Runtime Tests: PASSED"
else
    echo "Mode Transition Runtime Tests: FAILED"
fi
echo "=========================================="

exit $EXIT_CODE
