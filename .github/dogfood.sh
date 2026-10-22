#!/usr/bin/env bash
# dogfood.sh HARDWARE FAB_BEFORE CACHE: jlcplace on the board it was made for (dial-me-spinner).
#   The shipped CPLs check without a red row once SW1's switch numbering is recorded (a TS-1187A: EasyEDA numbers
#   its pins 1-4, KiCad 1,1,2,2, and which pins the switch joins is in its datasheet, not in the records), and the
#   old CPLs are red on exactly the 7 rows that were wrong (main J1 J20 J40, base J1 J2 J3 SW1). Both over the
#   Gerbers they were ordered with (plotted from the drill/place origin, the CPLs' origin): no origin finding.
# Writes cpl-<board>.xml/.json and old-<board>.json (derived data only) in the current directory.
set -euo pipefail
H=$1 BEFORE=$2 CACHE=$3
DB=$(mktemp -d)
jlcplace confirm "$H/kicad/base/dial-me-spinner-base.kicad_pcb" --ref SW1 --db "$DB" --by ci --source manual \
  --note "TS-1187A datasheet: pins 1-2 and 3-4 joined" --cache "$CACHE" --offline
for b in main base; do
  jlcplace check "$H/kicad/$b/dial-me-spinner-$b.kicad_pcb" "$H/fab/$b/dial-me-spinner-$b-CPL-JLCPCB.csv" \
    --bom "$H/fab/$b/dial-me-spinner-$b-BOM-JLCPCB.csv" --gerbers "$H/fab/$b/gerbers.zip" --db "$DB" \
    --cache "$CACHE" --offline \
    --junit "cpl-$b.xml" --json "cpl-$b.json" --github-annotations
  set +e
  jlcplace check "$H/kicad/$b/dial-me-spinner-$b.kicad_pcb" "$BEFORE/$b/dial-me-spinner-$b-CPL-JLCPCB.csv" \
    --gerbers "$BEFORE/$b/gerbers.zip" --cache "$CACHE" --offline --json "old-$b.json" -q
  code=$?
  set -e
  if [ "$code" != 2 ]; then echo "::error::old $b CPL: exit $code, expected 2"; exit 1; fi
done
python3 - <<'PY'
import json
want = {"main": {"J1", "J20", "J40"}, "base": {"J1", "J2", "J3", "SW1"}}
for b, refs in want.items():
    d = json.load(open(f"old-{b}.json"))
    got = {r["ref"] for r in d["results"] if r["verdict"] == "red"}
    assert got == refs and d["findings"] == [], (b, sorted(got), d["findings"])
print("old CPLs red on exactly", {b: sorted(r) for b, r in want.items()})
PY
