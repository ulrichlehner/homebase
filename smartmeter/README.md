# Smart meter → Home Assistant (Wireless M-Bus)

Receive the LINZ NETZ smart meter locally over Wireless M-Bus and feed it into Home Assistant:
live power plus import/export counters, usable in the Energy dashboard. Scraping the portal
(see the repo root) is the fallback, because it is fragile.

> **This repo is public.** Never commit, not even in commit messages, issues, examples or logs:
> the access code/key, meter ID, serial number, metering point number, address, received telegrams
> (even encrypted), readings, or IDs of neighbours' meters. Personal values live only in `.env`
> (template: `.env.example`) or in the Home Assistant config outside the repo. Use placeholders
> (`<METER-ID>`, `<KEY>`) or the public test vector below. Check `git diff --staged` before every commit.

## Constraints

- Apartment; the meter sits in a utility cabinet outside, no installation possible there.
  Reception only over the air from inside the apartment.
- Tests on a MacBook (Apple Silicon, bash 3.2) with an RTL-SDR dongle (R820T). Nothing is written
  outside this folder except via Homebrew. No `make install`, no global packages, no Docker
  (no USB passthrough on macOS).

## The interface (LINZ NETZ document LN-666, "Beschreibung Wireless M-Bus Schnittstelle")

- Wireless M-Bus **mode T1, 868.95 MHz**, one telegram every **5 s**
- Manufacturer code **DEV** (`B610`), device type **2** (electricity)
- AES-128-CBC (OMS security mode 5), IV = M field + A field + 8× access number
- Payload: serial number (= meter number, BCD), date/time, 1.8.0 import (Wh), 2.8.0 export (Wh),
  1.7.0 / 2.7.0 power (W)
- Key = personal access code from the service portal (activate the customer interface → pick the
  installation → continue). It is only **visible for 14 days**, so store it right away.
- wmbusmeters driver: **`amiplus`**
- Public test vector from LN-666:
  - key `F1046961A0FC34C200906266C1409E11`
  - telegram `3F44B6106987320001027A59003005A7A88A648E15D98354C5DA547B32E1E6FE2A20C2D7003798EBDF80E15FF900442481DF2AB3A0E2C3376A72B13AE0798E57E6`
  - result 18.565 kWh import, 16.604 kWh export. The PDF says 18561 Wh, which is a typo in the
    document. The example is one byte longer than its length field, so the wmbusmeters warning
    there is expected.

## Status (2026-10-06)

- Customer interface activated, access code retrieved.
- The meter is received reliably inside the apartment with gain `auto` (every 5 s, very good SNR).
- **Decryption with the real key confirmed:** check bytes OK, driver `amiplus`, serial number
  matches the meter number in the portal.
- Home Assistant already works with the
  [wmbusmeters HA add-on](https://github.com/wmbusmeters/wmbusmeters-ha-addon), an RTL-SDR dongle
  and a Raspberry Pi running Home Assistant.
- Findings from that setup:
  - The official **Mosquitto MQTT broker** add-on must be installed.
  - The meter ID the add-on needs is **not** the meter number or metering point number from the
    portal. Take it from the add-on log, from the telegram of the matching electricity meter.
  - Old readings from the CSV files may have to be merged into HA's history. Counters must line up
    with the live meter, or `total_increasing` breaks in the Energy dashboard.
- **Wrong or zero energy values, sometimes** (found 2026-10-06): see
  [Wrong energy values](#wrong-energy-values-and-the-custom-driver). Fix: custom driver
  `drivers/amiplus_linznetz.xmq`, verified against all captured telegrams and running in the add-on
  since 2026-10-06 evening; long-term behaviour in Home Assistant not yet confirmed.
- **Open:** compare the counters once against the meter display or the portal. Import looks
  suspiciously low and export is > 0. Find out whether the meter is new and whether there is a
  feed-in source.
- Diehl heat meters (DME, type Heat) also transmit nearby. Irrelevant here.

## Tool: `wmbus-test.sh`

Native test tool for the Mac. Needs Homebrew and the Xcode Command Line Tools.

| Command               | Purpose                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `setup`               | brew: librtlsdr, rtl_433, pkg-config, libxml2. Builds rtl-wmbus and wmbusmeters locally into `wmbus-tools/`. Logs newly installed brew packages to `.wmbus-brew-installed`. |
| `scan`                | Targeted search for DEV / type 2, rotates frequency × gain, locks on at the first hit. Saves to `wmbus-out/hits.hex` and `hits.jsonl`. |
| `monitor`             | All wM-Bus telegrams, unfiltered (rtl_433).                                                                                              |
| `raw`                 | Raw telegrams via rtl_sdr and rtl_wmbus into `wmbus-out/`.                                                                               |
| `analyze [KEY] [HEX]` | Offline decryption with wmbusmeters. Without KEY, `WMBUS_KEY` from `.env` is used; without HEX, the latest hit.                          |
| `cleanup`             | Removes logged brew packages, builds and logs (asks first).                                                                              |

Environment options: `FREQ`, `GAIN`, `FREQS`, `GAINS`, `DWELL`, `TARGET_M` (see the script header).

Quirks learned along the way:

- wmbusmeters `configure` needs `PKG_CONFIG_PATH` to include `$(brew --prefix libxml2)/lib/pkgconfig`
  (libxml2 is keg-only). The script does this.
- `git describe` warnings during the build come from the shallow clones and are harmless.
- bash 3.2: expand possibly empty arrays as `${arr[@]+"${arr[@]}"}`.
- `scan` uses python3 from the Xcode Command Line Tools.
- `.env` is only read (via `sed`), never sourced.

## Wrong energy values and the custom driver

LN-666 specifies the energy counters in Wh (VIF `0x03`). On the real meter three variants show up
(observed 2026-10-06), and only the first one matches the document:

| Variant | import record | export record | What the builtin `amiplus` reports |
|---|---|---|---|
| spec | `0E 03` | `0E 83 3C` | correct |
| wrong exponent | `0E 04` (10 Wh, inferred from a ×10 phase, not seen in a captured telegram) or `0E 07` (10⁴ Wh) | `0E 84 3C` / `0E 87 3C` | ×10 or ×10 000 too high |
| all zero | `0E 00`, digits `000000000000` | `0E 80 3C`, digits zero | 0 kWh |

- **Wrong exponent:** same BCD digits as in the spec variant, only the exponent differs. The
  meter switches from one telegram to the next, check bytes stay OK and the digits keep counting
  exactly, so it is not a radio or decoding error. Seen as three phases of about 26 minutes each,
  in a 1023-telegram capture 85 telegrams used `0E07` and 938 used `0E03`.
- **All zero:** seen afterwards for at least two minutes in a row, with a normal device clock,
  access number and power values. Whether this is a third meter state or something else is not
  known.
- Power values (`0B 2B`, `0B AB 3C`) were never affected.

wmbusmeters scales by VIF, as it should. In Home Assistant the first variant gives values 10× or
10 000× too high, the second one a drop to 0 and back. A `total_increasing` sensor counts a drop as a
reset and the return as consumption of the whole counter, so both variants add false energy to the
Energy dashboard.

**Fix at the source:** `drivers/amiplus_linznetz.xmq`

- reads the energy digits as Wh and ignores the VIF exponent (`vif_scaling = None` plus
  `force_scale = 1/1000` to get kWh). Do not use `override_vif_unit = wh` here: it only exists
  in wmbusmeters since 2026-09-25, and an older add-on image silently ignores it, which makes
  Home Assistant show Wh as kWh (values 1000× too high, seen on 2026-10-07),
- turns a counter value of 0 into `null` (`null_value = 0`), because a real counter is never 0;
  in Home Assistant the sensor then goes `unknown` for those telegrams instead of 0. If your
  meter legitimately exports 0 kWh, remove the `null_value` line from the export field,
- keeps the field names of `amiplus` (`total_energy_consumption_kwh`,
  `total_energy_production_kwh`), so entities keep their IDs; voltages and tariff counters are
  left out, the meter does not send them,
- uses a dummy `detect` triplet (`DEV,FE,02`) on purpose. With the real one (`DEV,01,02`)
  wmbusmeters removes the builtin `amiplus` as soon as the file is loaded, and a meter still
  configured as `amiplus` crashes in a restart loop (`No such driver amiplus`, exit status 5).
  The driver is therefore never auto-detected, you select it explicitly.

Tests:

```bash
smartmeter/tests/run.sh     # synthetic telegrams (tests/telegrams.txt), custom vs builtin amiplus
```

All wrong-exponent variants give the same kWh with the custom driver, the zero variant gives `null`;
builtin `amiplus` is ×10 / ×10 000 off or 0. Verified locally against every captured real
telegram (not committed): all decode, import and export are monotonic, and builtin `amiplus`
differs in exactly the wrong ones. The public LN-666 test vector gives 18.565 / 16.604 kWh with both
drivers. To try it on a telegram with your key:

```bash
DRIVER=drivers/amiplus_linznetz.xmq ./wmbus-test.sh analyze [KEY] [HEX]
```

## Install in Home Assistant (wmbusmeters add-on 3.0.0-RC1, HA OS)

Tested on HA OS 18.3, Core 2026.9.4, add-on `wmbusmeters-ha-addon` 3.0.0-RC1 with the official
Mosquitto broker. Order matters, steps 1 to 3 before step 4.

**1. Add the driver.** Add-on web UI → tab **Drivers** → add a driver:

- file name `amiplus_linznetz.xmq`, **with** the extension. The add-on stores exactly this name,
  and wmbusmeters only loads `*.xmq` files,
- content: `drivers/amiplus_linznetz.xmq` from this repo, Save.

The add-on copies `/data/drivers` to its `wmbusmeters.drivers.d` folder on every start.

**2. Move the config location into the HA configuration.** Tab **Home** →
`wmbusmeters config location` → set `/homeassistant/wmbusmeters`, save, restart the add-on.
This version mounts the HA configuration as `/homeassistant`. A location under `/config` is an
empty folder inside the container that is wiped on restart, so files you put there disappear. The
folder `/addon_configs/…_wmbusmeters` does not exist for this add-on either. Check on the HA
host that `ls /config/wmbusmeters/etc` shows `mqtt_discovery`.

**3. Add the MQTT discovery file.** The add-on creates the HA entities from
`mqtt_discovery/<driver>.json`. For an unknown driver it logs
`File …/mqtt_discovery/amiplus_linznetz.json not found` and **removes all sensors** of the meter.
Copy `ha/mqtt_discovery/amiplus_linznetz.json` to
`/config/wmbusmeters/etc/mqtt_discovery/amiplus_linznetz.json` on the HA host (Terminal & SSH,
File editor, Samba). The add-on only copies missing files, it does not overwrite yours. The file
keeps the `unique_id`s and device identifiers of `amiplus`, so entities and history stay.

**4. Switch the meter.** Tab **Home** → Meters → `driver = amiplus_linznetz`, `id`, `key`, `name`.
The `id` is the one from the add-on log, not the meter number from the portal. Save, restart the
add-on.

Naming: `name` becomes the MQTT topic (`wmbusmeters/<name>`), the device name and the prefix of all
entity names. A readable name such as `<Place> grid` works: Home Assistant shows
"<Place> grid energy import" and turns it into the entity ID `sensor.<place>_grid_energy_import`. Keep
it unique per meter (two meters with the same name publish to the same topic and overwrite each
other) and leave out the street and house number, because names show up in logs and screenshots.
Scheme `<Place> <Role>`: `<Place> grid` for the grid meter, later `<Place> PV` for a PV inverter,
with the municipality as the place. The discovery file names the entities `energy import`,
`energy export`, `power import` and `power export`.

**5. Check the add-on log:**

- no `No such driver`, no restart loop,
- `Add/update topic` for seven sensors (import, export, both powers, timestamp, device date,
  rssi), no `Removing topic`,
- ends with `Started auto rtlwmbus … listening on t1`.

To see telegrams, set `logtelegrams` to `true` in the Home tab for a while. The log lines contain
the raw decrypted telegram, counters included.

**6. Check Home Assistant.** Settings → Devices & services → MQTT → your meter:

- import, export and powers show values again, entity IDs unchanged (no `_2` suffixes),
- import and export are plausible against the meter display or the portal and only rise slowly,
- during zero telegrams the energy sensors show `unknown` instead of 0 (HA ignores `unknown`
  states in the statistics, to be confirmed in operation),
- the old voltage and tariff entities stay unavailable and can be deleted.

**Fallback without the discovery file:** define the MQTT sensors by hand in `configuration.yaml`
(`state_topic: wmbusmeters/<NAME>`, `value_template: "{{ value_json.<field> }}"`, import and export
with `device_class: energy`, `state_class: total_increasing`, `unit_of_measurement: kWh`; powers
with `device_class: power`, `state_class: measurement`, `kW`) and use the same `unique_id`s as in
`ha/mqtt_discovery/amiplus_linznetz.json`. Keep that YAML out of the repo, it contains your meter
name and ID.

**Pitfalls seen during the first install:**

| Symptom | Cause |
|---|---|
| restart loop, `No such driver amiplus … triggered a removal of the builtin driver`, exit status 5 | the driver declared `detect` = `DEV,01,02`, same as `amiplus` (fixed: dummy triplet) |
| `File …/amiplus_linznetz.json not found`, then `Removing topic …` | no discovery file for the new driver (step 3) |
| file under `/config/wmbusmeters` missing after restart | add-on `/config` is ephemeral (step 2) |
| the add-on log prints key, ID and meter name in clear text on every start | by design, never share or commit it |

**After it is confirmed in operation:**

- fix the HA statistics of the affected hours (Developer tools → Statistics → adjust the sum),
  otherwise the Energy dashboard keeps the spikes,
- remove the template-sensor fallback, if you set one up,

## Home Assistant integration

Preferred: no custom code.

1. **RTL-SDR on the HA host plus the wmbusmeters add-on** (needs Mosquitto). The HA host must be
   within radio range of the meter cabinet. Add-on configuration, roughly (check the current add-on
   docs for the exact option syntax):

   ```
   device=rtlwmbus
   name=strom
   driver=amiplus
   id=<METER-ID>      # from the add-on log, see findings above
   key=<KEY>
   ```

   Values go only into the HA configuration, never into the repo.

2. **Sensors:** check whether the add-on's MQTT discovery covers `amiplus`. If not, create MQTT
   sensors by hand: import and export with `device_class: energy` and `state_class: total_increasing`,
   power with `device_class: power` and `state_class: measurement`.

3. **Energy dashboard:** configure grid consumption and feed-in.

Fallbacks if the HA host is out of range: an ESP32 plus SX1276/SX1262 as a remote receiver → MQTT →
wmbusmeters bridge add-on, or the Smart-Meter-Adapter, variant MBUS (xDevelop, about 150 €, WLAN/MQTT).
If HA runs as a container, run wmbusmeters as its own container next to it.
