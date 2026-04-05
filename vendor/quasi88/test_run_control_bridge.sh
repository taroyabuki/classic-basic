#!/bin/bash
# Phase 18: Vendor bridge runtime contract test runner
# Tests the control bridge unit tests for proper contract implementation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"
BUILD_DIR="$SCRIPT_DIR/build_test"

PASS=0
FAIL=0

echo "========================================"
echo "Phase 18: Vendor Bridge Runtime Contract Test"
echo "========================================"
echo

# Create build directory
mkdir -p "$BUILD_DIR"

# Check if source files exist
if [ ! -f "$SRC_DIR/run_control_bridge.c" ]; then
    echo "[FAIL] run_control_bridge.c not found"
    exit 1
fi

if [ ! -f "$SRC_DIR/test_run_control_bridge.c" ]; then
    echo "[FAIL] test_run_control_bridge.c not found"
    exit 1
fi

echo "[INFO] Source files found"

# Check for placeholder comments that should be removed
echo
echo "Checking for placeholder/TODO markers..."
if grep -q "full implementation" "$SRC_DIR/run_control_bridge.c" 2>/dev/null; then
    # Allow "full implementation" only in comments about architecture
    if grep -E "full implementation" "$SRC_DIR/run_control_bridge.c" | grep -v "^\s*\*" | grep -q .; then
        echo "[WARN] Found 'full implementation' in code"
    fi
fi

if grep -qi "placeholder" "$SRC_DIR/run_control_bridge.c" 2>/dev/null; then
    echo "[FAIL] Found 'placeholder' marker in run_control_bridge.c - should be removed"
    FAIL=$(( FAIL + 1 ))
else
    echo "[PASS] No placeholder markers in run_control_bridge.c"
    PASS=$(( PASS + 1 ))
fi

# Check test file for ambiguous assertions
echo
echo "Checking test assertions..."
if grep -E "ok or|OK or|\.\.\. or|either.*or" "$SRC_DIR/test_run_control_bridge.c" | grep -v "^[[:space:]]*\*" | grep -q .; then
    echo "[FAIL] Found ambiguous 'or' assertions in test - must be explicit"
    FAIL=$(( FAIL + 1 ))
else
    echo "[PASS] Test assertions are explicit"
    PASS=$(( PASS + 1 ))
fi

# Check that LOAD command has proper implementation (not just file existence check)
echo
echo "Checking LOAD command implementation..."
if grep -A 30 "cmd_load" "$SRC_DIR/run_control_bridge.c" | grep -q "basic_encode_list\|basic_load_intermediate"; then
    echo "[PASS] LOAD command uses actual BASIC encoding functions"
    PASS=$(( PASS + 1 ))
else
    echo "[FAIL] LOAD command does not use actual BASIC encoding functions"
    FAIL=$(( FAIL + 1 ))
fi

# Check that STATE command uses tracked ROM ID (not hardcoded placeholder)
echo
echo "Checking STATE command ROM ID handling..."
if grep -A 15 "cmd_state" "$SRC_DIR/run_control_bridge.c" | grep -q "loaded_rom_id"; then
    echo "[PASS] STATE command uses tracked ROM ID"
    PASS=$(( PASS + 1 ))
else
    echo "[FAIL] STATE command does not use tracked ROM ID"
    FAIL=$(( FAIL + 1 ))
fi

# Check that WAIT command uses tracked ROM ID in response
echo
echo "Checking WAIT command ROM ID in response..."
if grep "ROM=%s" "$SRC_DIR/run_control_bridge.c" | grep -q "loaded_rom_id"; then
    echo "[PASS] WAIT command uses tracked ROM ID in response"
    PASS=$(( PASS + 1 ))
else
    echo "[FAIL] WAIT command does not use tracked ROM ID"
    FAIL=$(( FAIL + 1 ))
fi

# Check for clear failure paths in tests
echo
echo "Checking test failure path coverage..."
if grep -q "FILE_NOT_FOUND" "$SRC_DIR/test_run_control_bridge.c"; then
    echo "[PASS] Tests cover FILE_NOT_FOUND error path"
    PASS=$(( PASS + 1 ))
else
    echo "[FAIL] Tests do not cover FILE_NOT_FOUND error path"
    FAIL=$(( FAIL + 1 ))
fi

if grep -q "PROTOCOL" "$SRC_DIR/test_run_control_bridge.c"; then
    echo "[PASS] Tests cover PROTOCOL error path"
    PASS=$(( PASS + 1 ))
else
    echo "[FAIL] Tests do not cover PROTOCOL error path"
    FAIL=$(( FAIL + 1 ))
fi

echo
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
echo "========================================"

# Cleanup
rm -rf "$BUILD_DIR"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
