#!/usr/bin/env bash
# Runs the synthetic telegrams in telegrams.txt through the custom driver and the builtin
# amiplus driver. The custom driver must give identical kWh for all VIF variants; the builtin
# one is shown for comparison (it is expected to be wrong for 0E04/0E07).
# Needs wmbusmeters from ./wmbus-test.sh setup. Nothing is written outside this repo.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$(find "$DIR/wmbus-tools/wmbusmeters" -type f -name wmbusmeters -perm -u+x 2>/dev/null | head -n1)"
[ -n "$BIN" ] || { echo "wmbusmeters not found, run ./wmbus-test.sh setup first" >&2; exit 1; }
DRIVER="$DIR/drivers/amiplus_linznetz.xmq"

field() { # field <json> <name>
  printf '%s' "$1" | sed -n "s/.*\"$2\":\([-0-9.a-z]*\).*/\1/p"
}

fail=0
while read -r name hex want_in want_out; do
  case "$name" in ''|'#'*) continue ;; esac
  builtin_json="$("$BIN" --format=json "$hex" t amiplus 12345678 NOKEY 2>/dev/null || true)"
  custom_json="$("$BIN" --format=json --driver="$DRIVER" "$hex" t amiplus_linznetz 12345678 NOKEY 2>/dev/null || true)"
  got_in="$(field "$custom_json" total_energy_consumption_kwh)"
  got_out="$(field "$custom_json" total_energy_production_kwh)"
  if [ "$got_in" = "$want_in" ] && [ "$got_out" = "$want_out" ]; then status=OK; else status=FAIL; fail=1; fi
  printf '%-14s %-4s custom: %s / %s kWh   builtin amiplus: %s / %s kWh\n' "$name" "$status" \
    "${got_in:-?}" "${got_out:-?}" \
    "$(field "$builtin_json" total_energy_consumption_kwh)" "$(field "$builtin_json" total_energy_production_kwh)"
done < "$DIR/tests/telegrams.txt"
exit $fail
