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
- **Wrong or zero energy values, sometimes** (found 2026-10-06, analysed 2026-10-15): see
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
The real meter sends other energy records too, several times a day for about half an hour each.
In a log of four days (2026-10-12 to 16, 68,725 telegrams, one every 5 s, all kept anonymised in
[`samples/`](samples/README.md)) there were 32 such phases of 22 to 52 minutes:

| Record | Import | Export | Unit the VIF says | Phases | Digits |
|---|---|---|---|---|---|
| spec | `0E 03` | `0E 83 3C` | Wh | | the counter in Wh |
| VIF 4 to 7 | `0E 04` … `0E 07` | `0E 84 3C` … `0E 87 3C` | 10 to 10⁴ Wh | 8 | the counter in **Wh**, the VIF is wrong |
| VIF 7 | `0E 07` | `0E 87 3C` | 10⁴ Wh | 3 | the counter in **tenths of a Wh** (`1183890` at 118,389 Wh) |
| all zero | `0E 00` | `0E 80 3C` | 10⁻³ Wh | 21 | `000000000000` |

- In the wrong-VIF phases the digits continue the counter without a step at every switch (0 or
  1 Wh, 5 to 16 s apart) and keep counting with the consumption, in Wh or in tenths of a Wh.
- Since 2026-10-15 05:27 every phase was all zero.
- Earlier phases behaved differently. On 2026-10-09 an earlier version of this driver, which read
  the digits as Wh whatever the VIF said, showed a few Wh where the counter stood near 90 kWh:
  that matches a VIF 7 record whose digits really were in 10⁴ Wh. On 2026-10-11 it showed about
  a tenth of the counter, which matches VIF 4 with digits really in 10 Wh. So the meter sends the
  same records sometimes with the right digits for their VIF and sometimes with Wh digits and a
  wrong VIF, and sometimes in tenths of a Wh.
- One telegram alone does not tell which of these it is: `000000000008` can be 8 Wh with a
  wrong VIF or 8 × 10⁴ Wh with a right one. Telling them apart needs the previous counter value,
  and a wmbusmeters driver sees one telegram at a time. So no driver rule reads these records
  correctly every time; they can only be ignored.
- All zero: no counter at all, with normal device clock, access number and power values.
- Power values (`0B 2B`, `0B AB 3C`) were never affected; they keep coming every 5 s during all
  phases. The [LINZ NETZ Energy add-on](../linznetz_energy) uses them to split the consumption of
  such a gap between the hours (to about 1 Wh against the portal).

In Home Assistant a `total_increasing` sensor counts a drop as a meter reset and the return as
consumption of the whole counter, so every phase books tens or hundreds of kWh that never flowed.

**Fix at the source:** `drivers/amiplus_linznetz.xmq`

- the energy fields only match the records LN-666 specifies (`difvifkey = 0E03` and `0E833C`) and
  scale them normally (`vif_scaling = Auto`). Telegrams with any other energy record carry no
  new energy value; power and date still come through. The running wmbusmeters keeps the last
  value of a field, so the MQTT messages of such a phase repeat the last spec counter: in Home
  Assistant the energy sensors stay flat and continue when the meter returns to the spec (seen
  in the history: no `unknown`, no drop). The consumption of the phase lands in the telegram
  after it,
- keeps the field names of `amiplus` (`total_energy_consumption_kwh`,
  `total_energy_production_kwh`); voltages and tariff counters are left out, the meter does not
  send them,
- uses a dummy `detect` triplet (`DEV,FE,02`) on purpose. With the real one (`DEV,01,02`)
  wmbusmeters removes the builtin `amiplus` as soon as the file is loaded, and a meter still
  configured as `amiplus` crashes in a restart loop (`No such driver amiplus`, exit status 5).
  The driver is therefore never auto-detected, you select it explicitly.

Earlier versions read the digits as Wh whatever the VIF said (`vif_scaling = None`,
`force_scale = 1/1000`, `null_value = 0`). That is right for phases with a wrong VIF and wrong for
phases whose digits match their VIF (the drops to about 1/10 000 and 1/10 on 2026-10-09 and
2026-10-11), see above. Avoid `override_vif_unit` in any case: it only exists in wmbusmeters
since 2026-09-25, and an older add-on image silently ignores it.

Tests:

```bash
smartmeter/tests/run.sh     # synthetic telegrams (tests/telegrams.txt), custom vs builtin amiplus
```

`tests/telegrams_real.txt` adds 252 real telegrams (anonymised, around every switch between
record kinds). Run one at a time, the spec record gives the expected kWh and every other record
gives no value, while the builtin `amiplus` reports the wrong ones. All 68,725 telegrams of
[`samples/`](samples/README.md) as one wmbusmeters run (as in the add-on): every spec telegram
gives its counter, never decreasing, every other one repeats the last spec value. The public LN-666 test vector gives 18.565 / 16.604 kWh with both drivers. To try it
on a telegram with your key:

```bash
DRIVER=drivers/amiplus_linznetz.xmq ./wmbus-test.sh analyze [KEY] [HEX]
```

## Install in Home Assistant (wmbusmeters add-on 3.1.0, HA OS)

First installed with HA OS 18.3, Core 2026.9.4, add-on `wmbusmeters-ha-addon` 3.0.0-RC1 and the
official Mosquitto broker; updated to add-on 3.1.0 on 2026-10-16. Step 1 before step 2.

**1. Add the driver.** Add-on web UI → tab **Drivers** → add a driver:

- file name `amiplus_linznetz.xmq`, **with** the extension. The add-on stores exactly this name,
  and wmbusmeters only loads `*.xmq` files,
- content: `drivers/amiplus_linznetz.xmq` from this repo, Save.

The add-on copies `/data/drivers` to its `wmbusmeters.drivers.d` folder on every start. Leave
`wmbusmeters config location` at its default `/homeassistant/wmbusmeters`: the add-on rebuilds
that folder from its own settings on every start (a location under `/config`, from older
versions, is moved there automatically).

**2. Switch the meter.** Tab **Home** → Meters → `driver = amiplus_linznetz`, `id`, `key`, `name`.
The `id` is the one from the add-on log, not the meter number from the portal. Save, restart the
add-on.

Naming: `name` becomes the MQTT topic (`wmbusmeters/<name>`), the device name and the prefix of all
entity names. A readable name such as `<Place> grid` works: Home Assistant shows
"<Place> grid total energy consumption" and turns it into the entity ID
`sensor.<place>_grid_total_energy_consumption`. Keep
it unique per meter (two meters with the same name publish to the same topic and overwrite each
other) and leave out the street and house number, because names show up in logs and screenshots.
Scheme `<Place> <Role>`: `<Place> grid` for the grid meter, later `<Place> PV` for a PV inverter,
with the municipality as the place.

**Entities.** Since add-on 3.1.0 the MQTT discovery is generated from the driver, when the first
telegram of the meter arrives after a start ("Enable mqtt discovery" on, Home tab). No discovery
file is needed: the driver's fields give six entities, import and export (kWh,
`total_increasing`, `energy`), both powers (kW, `measurement`, `power`), the device date and time
and the signal strength. A telegram without a field keeps the entity's previous state. The
`unique_id`s are `wmbusmeters_<id>_<field>`, the same as with the stock `amiplus`.

Coming from add-on 3.0.x with the discovery file of earlier versions of this repo
(`/config/wmbusmeters/etc/mqtt_discovery/amiplus_linznetz.json`): the update keeps the energy,
power and signal entities with their entity IDs and history (seen on 2026-10-16: no `unknown`,
no drop, the counter ran through the update). The device date entity is replaced
(`device_date_time` → `device_datetime`), the timestamp entity is gone. The file is no longer
read and can be deleted.

### Differences from the stock `amiplus`

The driver stays as close to the stock `amiplus` as possible, so it is easy to compare with
upstream. Everything that differs:

| Change | Why |
|---|---|
| both energy fields match `difvifkey = 0E03` / `0E833C` instead of `vif_range = AnyEnergyVIF` | only the records from LN-666 are trustworthy |
| `detect` is a dummy triplet | the real one removes the builtin `amiplus` |
| voltage, tariff and reactive fields left out | the meter does not send them |

Field names, quantities and the `unique_id`s of the entities are those of the stock driver.

**3. Check the add-on log:**

- no `No such driver`, no restart loop,
- ends with `Started auto rtlwmbus … listening on t1`.

To see telegrams, set `logtelegrams` to `true` in the Home tab for a while. The log lines contain
the raw decrypted telegram, counters included, and the add-on's start lines show the key: keep
the log private. `tools/telegram_log.py` turns it into anonymised samples, see
[`samples/`](samples/README.md). Set it back to `false` afterwards.

**4. Check Home Assistant.** Settings → Devices & services → MQTT → your meter:

- import, export and powers show values, entity IDs unchanged (no `_2` suffixes),
- import and export are plausible against the meter display or the portal and only rise slowly,
- during telegrams with a non-spec energy record the energy sensors stay at the last value
  instead of showing a wrong one, and continue afterwards (seen in operation, no jumps),
- entities of the stock driver that this driver does not fill (voltage, tariffs) stay
  unavailable and can be deleted.

**Pitfalls seen during the first install:**

| Symptom | Cause |
|---|---|
| restart loop, `No such driver amiplus … triggered a removal of the builtin driver`, exit status 5 | the driver declared `detect` = `DEV,01,02`, same as `amiplus` (fixed: dummy triplet) |
| `File …/amiplus_linznetz.json not found`, then `Removing topic …` | add-on 3.0.x without the discovery file of earlier versions of this repo; update to 3.1.0 |
| file under `/config/wmbusmeters` missing after an add-on update | add-on 3.0.0-RC1 with a config location under `/config` (ephemeral); 3.1.0 moves it to `/homeassistant/wmbusmeters` |
| energy values 1000× too high after switching the driver | `override_vif_unit` is ignored by older wmbusmeters builds |
| hourly bars of 90 to 100 kWh, value briefly 1/10 or 1/10 000 of the counter | non-spec energy records read by an earlier driver version |
| entity IDs still carry the old name after deleting and re-adding the device | HA restores entity IDs via the `unique_id`; rename them by hand in the entity settings |
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

2. **Sensors:** with "Enable mqtt discovery" on, the add-on (3.1.0 or later) creates them from
   the driver, see [Install in Home Assistant](#install-in-home-assistant-wmbusmeters-add-on-310-ha-os).

3. **Energy dashboard:** configure grid consumption and feed-in.

   Better: the add-on in [`linznetz_energy/`](../linznetz_energy) takes this counter as its live
   source, cleans misread phases (wrong scale, near-zero values, repeated values), fills gaps from
   the LINZ NETZ portal and writes one statistic with the whole history for the Energy dashboard. A
   guard sensor in Home Assistant against the misread spikes is not needed then.

Fallbacks if the HA host is out of range: an ESP32 plus SX1276/SX1262 as a remote receiver → MQTT →
wmbusmeters bridge add-on, or the Smart-Meter-Adapter, variant MBUS (xDevelop, about 150 €, WLAN/MQTT).
If HA runs as a container, run wmbusmeters as its own container next to it.
