#!/usr/bin/env bash
# wmbus-test.sh — Linz-Netz-Smart-Meter (Wireless M-Bus) per RTL-SDR am Mac testen, nativ mit Homebrew.
#
#   ./wmbus-test.sh setup              # brew-Pakete + rtl_wmbus/wmbusmeters bauen (einmalig)
#   ./wmbus-test.sh scan               # gezielt nach dem Stromzähler suchen (Frequenz/Gain adaptiv)
#   ./wmbus-test.sh monitor            # alle Telegramme ungefiltert anzeigen
#   ./wmbus-test.sh raw                # Rohtelegramme loggen (rtl_wmbus)
#   ./wmbus-test.sh analyze [KEY] [HEX] # Entschlüsseln testen (KEY sonst WMBUS_KEY aus .env)
#   ./wmbus-test.sh cleanup            # alles wieder entfernen (Builds, Logs, brew-Pakete)
#
# Optionen per Umgebungsvariable:
#   FREQ=...         andere Frequenz (Default 868950000 = T1, laut Linz Netz korrekt)
#   GAIN=40          feste Verstärkung für monitor/raw (Default auto)
#   FREQS/GAINS/DWELL  Suchraster für scan (Default "868950000 868300000" / "auto 40 30 49.6 20" / 30 s)
#   TARGET_M=DEV     gesuchter Hersteller für scan
#
# Voraussetzung: Homebrew und Xcode Command Line Tools (xcode-select --install).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS="${TOOLS:-$DIR/wmbus-tools}"
OUT_DIR="${OUT_DIR:-$DIR/wmbus-out}"
FREQ="${FREQ:-868950000}"
GAIN="${GAIN:-auto}"
BREW_PKGS=(librtlsdr rtl_433 pkg-config libxml2)
BREW_STATE="$DIR/.wmbus-brew-installed"   # merkt sich, welche Pakete setup neu installiert hat

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

find_bin() {
  find "$TOOLS/$1" -type f -name "$2" -perm -u+x 2>/dev/null | head -n1
}

setup() {
  command -v brew >/dev/null || { echo "Homebrew fehlt: https://brew.sh" >&2; exit 1; }
  xcode-select -p >/dev/null 2>&1 || { echo "Xcode Command Line Tools fehlen: xcode-select --install" >&2; exit 1; }

  local pkg new=()
  for pkg in "${BREW_PKGS[@]}"; do
    brew list --formula "$pkg" >/dev/null 2>&1 || new+=("$pkg")
  done
  if [ ${#new[@]} -gt 0 ]; then
    brew install "${new[@]}"
    printf '%s\n' "${new[@]}" >> "$BREW_STATE"
  fi
  mkdir -p "$TOOLS"

  [ -d "$TOOLS/rtl-wmbus" ] || git clone --depth 1 https://github.com/xaelsouth/rtl-wmbus "$TOOLS/rtl-wmbus"
  make -C "$TOOLS/rtl-wmbus"

  [ -d "$TOOLS/wmbusmeters" ] || git clone --depth 1 https://github.com/wmbusmeters/wmbusmeters "$TOOLS/wmbusmeters"
  # libxml2 ist bei Homebrew keg-only, daher den pkg-config-Pfad explizit setzen
  (cd "$TOOLS/wmbusmeters" \
    && PKG_CONFIG_PATH="$(brew --prefix libxml2)/lib/pkgconfig:$(brew --prefix)/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" ./configure \
    && make)

  echo
  echo "rtl_wmbus:   $(find_bin rtl-wmbus rtl_wmbus)"
  echo "wmbusmeters: $(find_bin wmbusmeters wmbusmeters)"
}

monitor() {
  echo "Zeige alle wM-Bus-Telegramme auf $FREQ Hz. Abbruch mit Ctrl+C."
  local ids args=()
  ids=$(rtl_433 -R help 2>&1 | grep -i "m-bus" | grep -oE "\[[0-9]+\]" | tr -d "[]" || true)
  for i in $ids; do args+=(-R "$i"); done
  [ "$GAIN" = "auto" ] || args+=(-g "$GAIN")
  exec rtl_433 -f "$FREQ" -s 1200k ${args[@]+"${args[@]}"} -F json -M time:iso -M level
}

# Gezielte Suche nach dem Stromzähler: rotiert Frequenz und Gain, bis Telegramme
# vom Ziel-Hersteller (Default DEV, Typ 2 = Strom) kommen, und bleibt dann dort.
scan() {
  command -v rtl_433 >/dev/null || { echo "rtl_433 fehlt – zuerst ./wmbus-test.sh setup" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "python3 fehlt (kommt mit den Xcode Command Line Tools)" >&2; exit 1; }
  mkdir -p "$OUT_DIR"
  python3 - "$OUT_DIR" <<'PY'
import json, os, queue, re, subprocess, sys, threading, time
from datetime import datetime

OUT = sys.argv[1]
FREQS = os.environ.get("FREQS", "868950000 868300000").split()
GAINS = os.environ.get("GAINS", "auto 40 30 49.6 20").split()
DWELL = int(os.environ.get("DWELL", "30"))     # Sekunden pro Kombination; Zähler sendet alle 5 s
TARGET_M = os.environ.get("TARGET_M", "DEV")
TARGET_TYPE = 2                                 # Electricity
TTY = sys.stdout.isatty()

helptext = subprocess.run(["rtl_433", "-R", "help"], capture_output=True, text=True)
DECODERS = sorted(set(re.findall(r"\[(\d+)\]\*?\s+[^\n]*m-bus", (helptext.stdout + helptext.stderr).lower())))

meters = {}        # (M, id, type) -> dict(count, rssi, freqs)
hits = []
seen_per_combo = {}

def mhz(f): return f"{int(f) / 1e6:.2f} MHz"

def status(text):
    if TTY:
        sys.stdout.write("\r\033[K" + text)
        sys.stdout.flush()

def say(text):
    if TTY:
        sys.stdout.write("\r\033[K")
    print(text, flush=True)

def reader(stream, q, tag):
    for line in stream:
        q.put((tag, line))
    q.put((tag, None))

def run(freq, gain, duration, cycle):
    cmd = ["rtl_433", "-f", freq, "-s", "1200k", "-F", "json", "-M", "time:iso", "-M", "level"]
    for d in DECODERS:
        cmd += ["-R", d]
    if gain != "auto":
        cmd += ["-g", gain]
    if duration:
        cmd += ["-T", str(duration)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    q = queue.Queue()
    threading.Thread(target=reader, args=(p.stdout, q, "out"), daemon=True).start()
    threading.Thread(target=reader, args=(p.stderr, q, "err"), daemon=True).start()

    start = time.time()
    combo = (freq, gain)
    seen_per_combo.setdefault(combo, 0)
    errors, closed, combo_hits = [], 0, 0
    while closed < 2:
        try:
            tag, line = q.get(timeout=1)
        except queue.Empty:
            tag, line = None, None
        if tag and line is None:
            closed += 1
        elif tag == "err":
            errors.append(line.rstrip())
        elif tag == "out":
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "M" not in d:
                continue
            seen_per_combo[combo] += 1
            key = (d.get("M"), d.get("id"), d.get("type"))
            m = meters.setdefault(key, {"count": 0, "rssi": None, "type_string": d.get("type_string", "?"), "freqs": set()})
            m["count"] += 1
            m["freqs"].add(mhz(freq))
            if d.get("rssi") is not None:
                m["rssi"] = max(m["rssi"], d["rssi"]) if m["rssi"] is not None else d["rssi"]
            is_target = d.get("M") == TARGET_M and d.get("type") == TARGET_TYPE
            if is_target or d.get("type") == TARGET_TYPE:
                combo_hits += 1
                hits.append(d)
                label = "TREFFER" if is_target else "Stromzähler (anderer Hersteller)"
                if duration:   # Suchphase: ausführlich
                    say(f"\n>>> {label}: {d.get('M')} id={d.get('id')} | {mhz(freq)} | gain {gain} | "
                        f"RSSI {d.get('rssi', '?')} dB, SNR {d.get('snr', '?')} dB | {d.get('time', '')}")
                    say(f"    {d.get('data', '')}")
                else:          # fixiert: eine Zeile pro Telegramm
                    say(f"  {d.get('time', '')}  {d.get('M')} id={d.get('id')}  "
                        f"RSSI {d.get('rssi', '?')} dB  SNR {d.get('snr', '?')} dB")
                if d.get("data"):
                    with open(os.path.join(OUT, "hits.hex"), "a") as f:
                        f.write(d["data"] + "\n")
                with open(os.path.join(OUT, "hits.jsonl"), "a") as f:
                    f.write(json.dumps(d) + "\n")

        elapsed = int(time.time() - start)
        others = sum(1 for k in meters if not (k[0] == TARGET_M and k[2] == TARGET_TYPE))
        phase = f"{elapsed}/{duration} s, Runde {cycle}" if duration else f"{elapsed} s, fixiert"
        status(f"[{mhz(freq)} | gain {gain} | {phase}] {seen_per_combo[combo]} Telegramme hier, "
               f"{others} fremde Zähler gesamt, Treffer: {len(hits)}")

    p.wait()
    if p.returncode not in (0, None) and time.time() - start < 3:
        say("rtl_433 ist sofort beendet worden:\n  " + "\n  ".join(errors[-5:]))
        sys.exit(1)
    return combo_hits

def summary():
    say("\nZusammenfassung")
    if not meters:
        say("  Keine wM-Bus-Telegramme empfangen – Antenne, Position und Dongle prüfen.")
    for (M, mid, typ), m in sorted(meters.items(), key=lambda kv: -kv[1]["count"]):
        mark = "  <-- Ziel" if M == TARGET_M and typ == TARGET_TYPE else ""
        say(f"  {M} id={mid} ({m['type_string']}): {m['count']}x, bestes RSSI {m['rssi']} dB, "
            f"{', '.join(sorted(m['freqs']))}{mark}")
    if hits:
        say(f"  {len(hits)} Treffer gespeichert in {OUT}/hits.hex – weiter mit: ./wmbus-test.sh analyze <CODE>")

say(f"Suche nach {TARGET_M}/Typ {TARGET_TYPE} | Frequenzen {', '.join(mhz(f) for f in FREQS)} | "
    f"Gains {', '.join(GAINS)} | {DWELL} s je Kombination. Abbruch mit Ctrl+C.")
try:
    cycle = 1
    while True:
        for freq in FREQS:
            for gain in GAINS:
                if run(freq, gain, DWELL, cycle):
                    say(f"\nZähler gefunden – bleibe auf {mhz(freq)}, gain {gain}.")
                    while True:
                        run(freq, gain, None, cycle)
        say(f"\nRunde {cycle} ohne Treffer, starte neu.")
        cycle += 1
except KeyboardInterrupt:
    pass
summary()
PY
}

raw() {
  local bin gain_args=()
  bin="$(find_bin rtl-wmbus rtl_wmbus)"
  [ -n "$bin" ] || { echo "rtl_wmbus nicht gefunden – zuerst ./wmbus-test.sh setup" >&2; exit 1; }
  [ "$GAIN" = "auto" ] || gain_args=(-g "$GAIN")
  mkdir -p "$OUT_DIR"
  echo "Logge Rohtelegramme nach $OUT_DIR/telegrams.log, Hex-Werte nach telegrams.hex. Abbruch mit Ctrl+C."
  rtl_sdr -f "$FREQ" -s 1600000 ${gain_args[@]+"${gain_args[@]}"} - 2>/dev/null \
    | "$bin" 2>/dev/null \
    | awk -F";" -v hexfile="$OUT_DIR/telegrams.hex" '{ print; fflush(); h=$NF; sub(/^0x/, "", h); print h >> hexfile; close(hexfile) }' \
    | tee -a "$OUT_DIR/telegrams.log"
}

env_value() {
  # liest NAME=wert aus .env, ohne die Datei auszuführen
  [ -f "$DIR/.env" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$DIR/.env" | tail -n1 | tr -d "\"' "
}

analyze() {
  local bin key="${1:-}" hex="${2:-}"
  [ -n "$key" ] || key="$(env_value WMBUS_KEY)"
  [ -n "$key" ] || { echo "Kein Schlüssel: als Argument übergeben oder WMBUS_KEY in .env setzen." >&2; exit 1; }
  bin="$(find_bin wmbusmeters wmbusmeters)"
  [ -n "$bin" ] || { echo "wmbusmeters nicht gefunden – zuerst ./wmbus-test.sh setup" >&2; exit 1; }
  if [ -z "$hex" ]; then
    local src="$OUT_DIR/hits.hex"
    [ -s "$src" ] || src="$OUT_DIR/telegrams.hex"
    [ -s "$src" ] || { echo "Kein HEX angegeben und keine gespeicherten Telegramme in $OUT_DIR." >&2; exit 1; }
    hex="$(tail -n1 "$src")"
    echo "Verwende letztes Telegramm aus $(basename "$src"): $hex"
  fi
  "$bin" "--analyze=$key" "$hex"
}

confirm() {
  local answer
  read -r -p "$1 [j/N] " answer
  [[ "$answer" =~ ^[jJyY]$ ]]
}

cleanup() {
  local pkgs=()

  # 1. brew-Pakete
  if [ -s "$BREW_STATE" ]; then
    while IFS= read -r pkg; do [ -n "$pkg" ] && pkgs+=("$pkg"); done < <(sort -u "$BREW_STATE")
    echo "Von setup neu installierte brew-Pakete: ${pkgs[*]}"
  else
    echo "Kein Protokoll vorhanden, welche brew-Pakete setup neu installiert hat."
    echo "Kandidaten: ${BREW_PKGS[*]}"
    echo "Achtung: Falls du einige davon schon vorher hattest, nicht entfernen."
    for pkg in "${BREW_PKGS[@]}"; do
      brew list --formula "$pkg" >/dev/null 2>&1 && confirm "  $pkg entfernen?" && pkgs+=("$pkg")
    done
  fi

  if [ ${#pkgs[@]} -gt 0 ] && confirm "Diese Pakete deinstallieren: ${pkgs[*]}?"; then
    local present=()
    for pkg in "${pkgs[@]}"; do
      brew list --formula "$pkg" >/dev/null 2>&1 && present+=("$pkg")
    done
    # In einem Aufruf, damit brew die Reihenfolge selbst auflöst (rtl_433 vor librtlsdr).
    # Hängt ein fremdes Paket von einem davon ab, bricht brew ab – dann einzeln versuchen.
    if [ ${#present[@]} -gt 0 ] && ! brew uninstall "${present[@]}"; then
      for pkg in "${present[@]}"; do
        brew list --formula "$pkg" >/dev/null 2>&1 || continue
        brew uninstall "$pkg" || echo "  $pkg bleibt installiert (wird von anderem Paket benötigt)."
      done
    fi
    rm -f "$BREW_STATE"

    # 2. automatisch mitinstallierte Abhängigkeiten (z. B. libusb)
    local orphans
    orphans="$(brew autoremove --dry-run 2>/dev/null | grep -v '^==>' || true)"
    if [ -n "$orphans" ]; then
      echo "Nicht mehr benötigte Abhängigkeiten laut brew:"
      echo "$orphans" | sed 's/^/  /'
      echo "Hinweis: Das kann auch verwaiste Pakete aus früheren Installationen enthalten."
      confirm "Diese ebenfalls entfernen?" && brew autoremove
    fi
  fi

  # 3. Builds und Logs
  if [ -d "$TOOLS" ] && confirm "Build-Ordner $TOOLS löschen?"; then
    rm -rf "$TOOLS"
  fi
  if [ -d "$OUT_DIR" ] && confirm "Logs in $OUT_DIR löschen?"; then
    rm -rf "$OUT_DIR"
  fi

  echo "Fertig. Heruntergeladene brew-Archive im Cache entfernt bei Bedarf: brew cleanup"
}

case "${1:-}" in
  setup)   setup ;;
  scan)    scan ;;
  monitor) monitor ;;
  raw)     raw ;;
  analyze) shift; analyze "$@" ;;
  cleanup) cleanup ;;
  *)       usage ;;
esac
