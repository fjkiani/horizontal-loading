#!/bin/sh
# Regenerate all 16 traps under the hardened gate, then run the secondary
# evaluation loop. Both stages checkpoint to JSON after every category, so an
# interrupt costs at most the category in flight.
set -e
cd /workspace/seal_deploy
echo "=== run_category_traps ==="
python run_category_traps.py
echo
echo "=== evaluate_traps ==="
python evaluate_traps.py
