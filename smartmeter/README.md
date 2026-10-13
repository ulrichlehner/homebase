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
- **Wrong or zero energy values, sometimes** (found 2026-10-06, understood 2026-10-12): see
  [Wrong energy values](#wrong-energy-values-and-the-custom-driver). Fix: custom driver
  `drivers/amiplus_linznetz.xmq` that only accepts the energy records from LN-666, verified
  against all captured telegrams.
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

LN-666 specifies the energy counters in Wh: DIF/VIF `0E 03` for import, `0E 83 3C` for export.
The real meter sends other records too, for minutes at a time:

| Record | Import | Export | Digits | What the builtin `amiplus` reports |
|---|---|---|---|---|
| spec | `0E 03` | `0E 83 3C` | Wh | correct |
| VIF 7 | `0E 07` (10⁴ Wh) | `0E 87 3C` | **not** in 10⁴ Wh: seen as Wh (2026-10-06) and as tenths of Wh (2026-10-12) | ×10 000 or ×100 000 too high |
| all zero | `0E 00` | `0E 80 3C` | `000000000000` | 0 kWh |

- The VIF 7 phases lasted about 26 to 30 minutes each. Within a phase the digits keep counting,
  but their unit is not the one the VIF says, and it differs between phases. Neither the VIF nor
  a fixed factor gives the right value, so these records cannot be repaired, only ignored.
- Three more phases have no logged telegrams, so their record is unknown: on 2026-10-06 the
  builtin driver showed values 10× too high, and on 2026-10-09 and 2026-10-11 the earlier version
  of this driver showed roughly 1/10 000 and 1/10 of the real counter. Presumably the same
  pattern with yet other digit units; the strict match drops them as long as the VIF is not 3.
- All zero: seen for single telegrams and for minutes, with normal device clock, access number and
  power values.
- Power values (`0B 2B`, `0B AB 3C`) were never affected. In 1721 logged telegrams all 1290
  `0E 03` records were monotonic and plausible.

In Home Assistant a `total_increasing` sensor counts a drop as a meter reset and the return as
consumption of the whole counter, so every phase books tens or hundreds of kWh that never flowed.

**Fix at the source:** `drivers/amiplus_linznetz.xmq`

- the energy fields only match the records LN-666 specifies (`difvifkey = 0E03` and `0E833C`) and
  scale them normally (`vif_scaling = Auto`). Telegrams with any other energy record carry no
  energy value; power and date still come through. In Home Assistant the energy sensors show
  `unknown` for those telegrams, and the counter continues when the meter returns to the spec,
- keeps the field names of `amiplus` (`total_energy_consumption_kwh`,
  `total_energy_production_kwh`); voltages and tariff counters are left out, the meter does not
  send them,
- uses a dummy `detect` triplet (`DEV,FE,02`) on purpose. With the real one (`DEV,01,02`)
  wmbusmeters removes the builtin `amiplus` as soon as the file is loaded, and a meter still
  configured as `amiplus` crashes in a restart loop (`No such driver amiplus`, exit status 5).
  The driver is therefore never auto-detected, you select it explicitly.

Earlier versions read the digits as Wh whatever the VIF said (`vif_scaling = None`,
`force_scale = 1/1000`, `null_value = 0`). That fixed the 2026-10-06 phases but turned the later
ones into drops to 1/10 and 1/10 000. Avoid `override_vif_unit` in any case: it only exists in
wmbusmeters since 2026-09-25, and an older add-on image silently ignores it.

Tests:

```bash
smartmeter/tests/run.sh     # synthetic telegrams (tests/telegrams.txt), custom vs builtin amiplus
```

The spec record gives the expected kWh, every other record gives no value, while the builtin
`amiplus` reports the wrong ones. Verified locally against every captured real telegram (not
committed): all `0E 03` records decode and are monotonic, all `0E 07` and `0E 00` records give no
energy value. The public LN-666 test vector gives 18.565 / 16.604 kWh with both drivers. To try it
on a telegram with your key:

```bash
DRIVER=drivers/amiplus_linznetz.xmq ./wmbus-test.sh analyze [KEY] [HEX]
```

## Install in Home Assistant (wmbusmeters add-on 3.0.0-RC1, HA OS)

Tested on HA OS 18.3, Core 2026.9.4, add-on `wmbusmeters-ha-addon` 3.0.0-RC1 with the official
Mosquitto broker. The add-on is configured in its own web UI (Open Web UI), not in the add-on
settings.

The add-on only publishes the readings to `wmbusmeters/<name>`. The Home Assistant sensors come
from `configuration.yaml` (`ha/mqtt_sensors.yaml`), not from the add-on's MQTT discovery. The
discovery needs a file `mqtt_discovery/<driver>.json`, which the add-on ships for its builtin
drivers only, so a custom driver needs a hand-placed file. The add-on's test channel (3.0.0.216)
replaces those files with payloads it generates from the driver when the first telegram arrives,
so entity names, `unique_id`s and value templates may change with an add-on update. YAML sensors
depend on neither mechanism, only on the topic and the field names of the driver.

**1. Add the driver.** Add-on web UI → tab **Drivers** → add a driver:

- file name `amiplus_linznetz.xmq`, **with** the extension. The add-on stores exactly this name,
  and wmbusmeters only loads `*.xmq` files,
- content: `drivers/amiplus_linznetz.xmq` from this repo, Save.

The add-on keeps drivers in its own `/data/drivers` and copies them to its config location on every
start, so they survive updates. The config location itself (tab **Home**) can stay at its default
`/homeassistant/wmbusmeters`; nothing is placed there by hand any more. The add-on rewrites its
`etc/` folder on every start and migrates an old `/config/...` location by itself.

**2. Configure the meter.** Tab **Home** → Meters → `driver = amiplus_linznetz`, `id`, `key`, `name`.
The `id` is the one from the add-on log, not the meter number from the portal. Leave **MQTT
discovery** off. Save, restart the add-on.

Naming: `name` becomes the MQTT topic (`wmbusmeters/<name>`), the device name and the prefix of all
entity names. A readable name such as `<Place> grid` works: Home Assistant shows
"<Place> grid total energy consumption" and turns it into the entity ID
`sensor.<place>_grid_total_energy_consumption`. Keep
it unique per meter (two meters with the same name publish to the same topic and overwrite each
other) and leave out the street and house number, because names show up in logs and screenshots.
Scheme `<Place> <Role>`: `<Place> grid` for the grid meter, later `<Place> PV` for a PV inverter,
with the municipality as the place. The entity names are those of the stock `amiplus` discovery file.

**3. Add the sensors to `configuration.yaml`.** Copy the block from `ha/mqtt_sensors.yaml` into
`/config/configuration.yaml` (File editor or Terminal & SSH add-on) and replace `<NAME>` and
`<METER-ID>`. If the file already has an `mqtt:` key, add the list items to its `sensor:` list.
Then Developer tools → YAML → Check configuration, and reload **Manually configured MQTT
entities** (or restart Home Assistant). The filled-in block contains the meter name and ID: keep it
out of this repo.

The template keeps the names, `unique_id`s and device identifiers the add-on's discovery used, so a
switch from discovery keeps entity IDs and history (see below). Validated against the MQTT sensor
schema of Home Assistant 2026.2.

**4. Check the add-on log:**

- no `No such driver`, no restart loop,
- no `Add/update topic` lines (discovery is off),
- ends with `Started auto rtlwmbus … listening on t1`.

To see telegrams, set `logtelegrams` to `true` in the Home tab for a while. The log lines contain
the raw decrypted telegram, counters included.

**5. Check Home Assistant.** Settings → Devices & services → MQTT → your meter:

- import, export and powers show values, entity IDs as expected (no `_2` suffixes),
- import and export are plausible against the meter display or the portal and only rise slowly,
- during telegrams with a non-spec energy record the energy sensors show `unknown` instead of a
  wrong value; HA ignores `unknown` states in the statistics (seen in operation, no jumps).

### Switching from the add-on's MQTT discovery

For an install that still uses discovery (stock `amiplus`, or `amiplus_linznetz` with the discovery
file from an earlier version of this folder). Order matters: if HA loads the YAML sensors while
the discovered ones exist, it rejects them as duplicate `unique_id`s.

1. Add the driver (step 1) and switch the meter to `amiplus_linznetz` (step 2) if not done yet.
2. Paste the block from step 3 into `configuration.yaml`, but do not reload or restart HA yet.
3. Add-on Home tab → MQTT → turn discovery off, save, restart the add-on. On every start the add-on
   deletes the discovery topics it did not publish itself, so the log shows `Removing topic …` for
   each sensor and HA removes the discovered entities.
4. Reload **Manually configured MQTT entities** (or restart HA). The sensors come back with the same
   `unique_id`s; HA restores their entity IDs, and the statistics continue because they are keyed
   by the entity ID.
5. Check step 5. If an entity ID got a `_2` suffix, delete the stale entity and rename the new one
   in the entity settings. The old voltage and tariff entities of the stock driver stay
   unavailable and can be deleted.
6. Optional cleanup on the HA host: `/config/wmbusmeters/etc/mqtt_discovery/amiplus_linznetz.json`
   is no longer used.

### Differences from the stock `amiplus`

The driver and the sensors stay as close to the stock `amiplus` as possible, so they are easy to
compare with upstream. Everything that differs:

| Where | Change | Why |
|---|---|---|
| driver, both energy fields | match `difvifkey = 0E03` / `0E833C` instead of `vif_range = AnyEnergyVIF` | only the records from LN-666 are trustworthy |
| driver | `detect` is a dummy triplet | the real one removes the builtin `amiplus` |
| driver | voltage, tariff and reactive fields left out | the meter does not send them |
| sensors | YAML in `configuration.yaml` instead of the add-on's discovery file | independent of how the add-on finds discovery files |
| sensors | only the 7 matching entities | same reason as the driver fields |
| sensors | `value_template` of import and export is `{{ value_json.get('<field>') }}` | the stock one falls back to the tariff sums, which fail when the field is missing |
| sensors | manufacturer and model texts say Develco and LINZ NETZ | the stock ones say Apator |

Everything else, including entity names, icons, device classes and `unique_id`s, is copied from the
stock `amiplus` discovery file.

**Pitfalls seen during the first install:**

| Symptom | Cause |
|---|---|
| restart loop, `No such driver amiplus … triggered a removal of the builtin driver`, exit status 5 | the driver declared `detect` = `DEV,01,02`, same as `amiplus` (fixed: dummy triplet) |
| `File …/amiplus_linznetz.json not found`, then `Removing topic …` | discovery is on for a custom driver; turn it off and use the YAML sensors |
| YAML sensors missing, log says the `unique_id` already exists | the discovered entities still exist; turn discovery off and restart the add-on first |
| energy values 1000× too high after switching the driver | `override_vif_unit` is ignored by older wmbusmeters builds |
| hourly bars of 90 to 100 kWh, value briefly 1/10 or 1/10 000 of the counter | non-spec energy records read by an earlier driver version |
| entity IDs still carry the old name after deleting and re-adding the device | HA restores entity IDs via the `unique_id`; rename them by hand in the entity settings |
| the add-on log prints key, ID and meter name in clear text on every start | by design, never share or commit it |

**After it is confirmed in operation:** fix the HA statistics of the affected hours (Developer
tools → Statistics → adjust the sum), otherwise the Energy dashboard keeps the spikes.

**Energy dashboard:** grid consumption = total energy consumption, return to grid = total energy
production (only with a feed-in source), live power from the two power sensors.

**If the HA host is out of range:** an ESP32 plus SX1276/SX1262 as a remote receiver → MQTT →
wmbusmeters bridge add-on, or the Smart-Meter-Adapter, variant MBUS (xDevelop, about 150 €,
WLAN/MQTT). If HA runs as a container, run wmbusmeters as its own container next to it. The YAML
sensors work unchanged as long as the readings arrive on `wmbusmeters/<name>`.
