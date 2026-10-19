#!/usr/bin/env bash
# Copies the add-on into build/ha-app/linznetz_energy_dev as the local app
# "LINZ NETZ Energy (dev)" (slug linznetz_energy_dev), which the Supervisor of the
# "HA app test" dev container builds from the working tree:
#
# - without the `image:` line of config.yaml, so it is built and not downloaded,
# - with lib/ and linznetz_energy/app/ next to the Dockerfile, which expects the repository
#   root as its build context, while the Supervisor builds in the app folder.
#
# build/ is ignored by git. Run it again after every change, then rebuild the app.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/build/ha-app/linznetz_energy_dev"
SRC="$ROOT/linznetz_energy"

rm -rf "$OUT"
mkdir -p "$OUT/linznetz_energy"
cp -R "$ROOT/lib" "$OUT/lib"
cp -R "$SRC/app" "$OUT/linznetz_energy/app"
cp -R "$SRC/translations" "$OUT/translations"
cp "$SRC/Dockerfile" "$SRC/DOCS.md" "$SRC/icon.png" "$SRC/logo.png" "$OUT/"
find "$OUT" \( -name __pycache__ -o -name '*.egg-info' -o -name .pytest_cache \) -prune -exec rm -rf {} +

sed -e '/^image:/d' \
    -e 's/^slug: linznetz_energy$/slug: linznetz_energy_dev/' \
    -e 's/^name: \(.*\)$/name: \1 (dev)/' \
    "$SRC/config.yaml" > "$OUT/config.yaml"
# the AppArmor profile is named after the slug
sed -e 's/^profile linznetz_energy /profile linznetz_energy_dev /' "$SRC/apparmor.txt" > "$OUT/apparmor.txt"

if ! grep -q '^slug: linznetz_energy_dev$' "$OUT/config.yaml" || grep -q '^image:' "$OUT/config.yaml" \
   || ! grep -q '^profile linznetz_energy_dev ' "$OUT/apparmor.txt"; then
  echo "prepare-app.sh: config.yaml or apparmor.txt not as expected" >&2
  exit 1
fi
echo "Prepared $OUT"
