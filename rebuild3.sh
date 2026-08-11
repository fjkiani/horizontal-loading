#!/bin/sh
# Full rebuild under the 13-test battery (T0b population-completeness added)
# and the fully-enumerated health population. Each stage checkpoints to JSON
# after every category, so an interrupt costs at most the category in flight.
set -e
cd /workspace/seal_deploy
export SEAL_NET_CACHE=/workspace/seal_cache

echo "=== stage 1: run_category_traps ==="
python run_category_traps.py
echo

echo "=== stage 2: evaluate_traps (13 tests) ==="
python evaluate_traps.py
echo

echo "=== stage 3: bake_catalog ==="
python bake_catalog.py
echo

echo "=== stage 4: pytest ==="
python -m pytest -q
echo

echo "=== verdict tally ==="
python - <<'PY'
import json, collections
rep = json.load(open("evaluation_report.json"))
per = rep["per_trap"]
tally = collections.Counter(r["verdict"] for r in per)
print("verdicts:", dict(tally))
print()
hdr = f"{'category':<28}{'verdict':<10}{'tier':<12}{'answer':<16}{'n':>7}  T0b"
print(hdr)
print("-" * len(hdr))
for r in sorted(per, key=lambda x: x["category"]):
    t0b = (r.get("tests") or {}).get("T0b_population_complete") or {}
    mark = {True: "pass", False: "FAIL", None: "unproven"}.get(t0b.get("pass"), "-")
    print(f"{r['category']:<28}{r['verdict']:<10}{str(r.get('witness_tier')):<12}"
          f"{str(r.get('answer'))[:15]:<16}{str((r.get('evidence') or {}).get('n_base')):>7}  "
          f"{mark}  {str(t0b.get('detail'))[:70]}")
PY
echo "=== done ==="
