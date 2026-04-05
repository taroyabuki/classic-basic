#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Vendor Proof Tests: Queue and State"
echo "=========================================="
echo ""
echo "[1/2] Building direct assertion test binary"
make -f Makefile.test clean test_queue_state
echo ""
echo "[2/2] Running direct assertion test binary"
./test_queue_state
