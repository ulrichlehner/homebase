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
- **Wrong energy scaling, sometimes** (found 2026-10-06): see [Wrong energy VIF](#wrong-energy-vif-and-the-custom-driver).
  Fix: custom driver `drivers/amiplus_linznetz.xmq`, verified against all captured telegrams,
  not yet running in Home Assistant.
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

## Wrong energy VIF and the custom driver

LN-666 specifies the energy counters in Wh (VIF `0x03`). The meter sometimes sends the same BCD
digits with a different VIF exponent: `0E04` (10 Wh, inferred from the ×10 phase, not seen in a
captured telegram) or `0E07` (10⁴ Wh). The same happens for export (`0E83 3C` / `0E84 3C` /
`0E87 3C`). It switches from one telegram to the next, check bytes stay OK and the digits keep
counting exactly, so it is not a radio or decoding error. Power values are never affected.
wmbusmeters scales by VIF, as it should, so Home Assistant gets ×10 or ×10 000 values and
`total_increasing` sensors jump up and then count as a reset.

Observed on 2026-10-06: three phases of about 26 minutes each (×10, ×10⁴, ×10⁴). In a 1023-telegram
capture 85 telegrams used `0E07` and 938 used `0E03`.

**Fix at the source:** `drivers/amiplus_linznetz.xmq` reads the energy digits as Wh and ignores the
VIF exponent (`vif_scaling = None`, `override_vif_unit = wh`). Field names match `amiplus`
(`total_energy_consumption_kwh`, `total_energy_production_kwh`), so entities keep their IDs.
Voltages and tariff counters are left out, the meter does not send them.

Tests:

```bash
smartmeter/tests/run.sh     # synthetic telegrams (tests/telegrams.txt), custom vs builtin amiplus
```

All three variants give the same kWh with the custom driver; builtin `amiplus` is ×10 / ×10 000 off
for `0E04` / `0E07`. Verified locally against all captured real telegrams (not committed): every
one decodes, import and export are monotonic with the custom driver, and builtin `amiplus` differs in
exactly the `0E07` ones. The public LN-666 test vector gives 18.565 / 16.604 kWh with both drivers.

Try it on a telegram with your key:

```bash
DRIVER=drivers/amiplus_linznetz.xmq ./wmbus-test.sh analyze [KEY] [HEX]
```

### Install in the Home Assistant add-on

1. Add-on web UI → tab **Drivers** → add a driver named `amiplus_linznetz.xmq`, paste the content
   of `drivers/amiplus_linznetz.xmq`. The add-on copies `/data/drivers` to
   `wmbusmeters.drivers.d` on start.
2. **MQTT discovery file.** The add-on creates the HA entities from `<driver>.json`. Without one
   it logs `File …/mqtt_discovery/amiplus_linznetz.json not found` and removes all sensors of the
   meter. Copy `ha/mqtt_discovery/amiplus_linznetz.json` to
   `/config/wmbusmeters/etc/mqtt_discovery/amiplus_linznetz.json` on the HA host (File editor or
   Samba share; the add-on only copies files that are missing, it does not overwrite yours). The
   file keeps the `unique_id`s and device identifiers of `amiplus`, so entities and history stay.
3. Meters → change the driver of the electricity meter from `amiplus` to `amiplus_linznetz`.
   Do this after step 2, otherwise the sensors are removed (see above).
4. Restart the add-on and check the log: no `No such driver`, discovery topics are added again,
   telegrams decode with plausible kWh values.
5. Check in HA that the existing entities kept their IDs and come back from "unavailable". Voltage
   and tariff entities of the old driver stay unavailable and can be deleted.

The driver deliberately uses a dummy `detect` triplet (`DEV,FE,02`). With the real one
(`DEV,01,02`), wmbusmeters removes the builtin `amiplus` as soon as the file is loaded, and a meter
still configured as `amiplus` crashes in a restart loop (`No such driver amiplus`, exit status 5).
This happened in the first install attempt.

After it is confirmed in operation:

- Fix the HA statistics of the affected hours (Developer tools → Statistics, adjust or delete the
  jumped values), otherwise the Energy dashboard keeps the spikes.
- Remove the template-sensor fallback (the one that divides by 10 above 1.5× the last good value).

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
