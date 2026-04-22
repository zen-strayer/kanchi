#!/bin/bash
set -euo pipefail

echo "========================================="
echo "Running Kanchi Backend Tests"
echo "========================================="
echo ""

cd "$(dirname "$0")"

echo "Running unit tests..."
uv run pytest tests/ -v

echo ""
echo "========================================="
echo "✓ All tests passed!"
echo "========================================="
