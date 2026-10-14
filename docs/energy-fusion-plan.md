# Plan: portal history + live RF readings in the Home Assistant Energy tab

Goal: one view in the HA Energy tab with the best possible data quality, from the first
reconstructed day (March 2023) up to the current hour:

1. long history (portal readings, the CSV archive and the reconstructed chart history),
2. gaps and glitches of the wM-Bus radio readings (RF) filled by the portal values,
3. RF readings validated against the portal.

Constraints: runs inside Home Assistant OS as a normal add-on from a custom add-on repository
(no HACS, no custom integration, HA not exposed to the internet). Every derived file is
non-destructive and repeatable: inputs are never modified, the same inputs give the same output.

## Design in short

The add-on writes **external statistics** (like the Opower integration does). It never writes
into the statistics of the RF sensor, whose running `sum` would have to be shifted for every
filled gap and which the recorder keeps extending every hour.

Every hour in the fused series takes the best source available:

| Period / case                                   | Source                                        |
| ----------------------------------------------- | --------------------------------------------- |
| March to August 2023                            | 6h chart blocks split by a typical profile    |
| September 2023 to the last portal day           | portal, measured quarter hours                |
| portal hour with a substitute value (Ersatzwert) | RF if valid, else the portal estimate         |
| after the last portal day up to now             | RF, until the portal replaces it              |

The fused series is rebuilt completely on every run (about 30k hourly rows), so a late portal
value or a corrected rule fixes the past automatically. Data quality per hour stays in the CSV
files, HA statistics have no field for it.

## Names

| What                         | ID                                         | Notes                                     |
| ---------------------------- | ------------------------------------------ | ----------------------------------------- |
| RF sensors (existing)        | `sensor.<place>_grid_*`                    | unchanged, stock amiplus entity names     |
| portal only                  | `linznetz:<place>_grid_energy_portal`      | external statistic, kWh, `sum`            |
| fused, used in the Energy tab | `linznetz:<place>_grid_energy_fused`       | external statistic, kWh, `sum`            |
| fused power (optional)       | `linznetz:<place>_grid_power_fused`        | external statistic, kW, `mean`/`min`/`max` |
| RF vs portal deviation       | `sensor.<place>_grid_rf_deviation`         | MQTT discovery, published by the add-on   |

The RF entities keep their IDs: renaming them gains nothing (the `linznetz:` prefix already
separates the derived statistics) and every rename risks the existing history. The display names
of the external statistics say `(portal)` / `(fused)` so the Energy tab never shows two entries
that look the same. Everything takes the site as a parameter, a second site (with PV feed-in)
can be added later.

## Steps and progress

Each step ends with something that can be checked before the next one starts. One branch and
one PR per step. Nothing touches the live HA setup before step 7.

### Step 0: Prerequisites

- [x] HA host: Raspberry Pi 5, `aarch64` (add-on image must be built for it)
- [x] RF entity IDs: `sensor.<place>_grid_*`
- [ ] Full HA backup before the first statistics import (step 3)

### Step 1: Split the 6h chart blocks into quarter hours (databot, offline)

- [x] `disaggregate.py`: typical profile per quarter hour of the day, by month and day type
      (workday, weekend or Austrian public holiday), from a fixed window of measured portal data
- [x] Each 6h block is split by the profile weights of its quarter hours, the block total stays
      exact (rounding with largest remainder), output `<meterId>_reconstructed_15min.csv` with
      `method=profile` and a note naming the profile
- [x] Backtest: measured days summed into 6h blocks, split again, compared with the real hourly
      values; reports the error next to a plain uniform split
- [ ] `merge` uses the 15-minute file instead of the 6h blocks when it exists
- [ ] Decide the profile variant (see the backtest results in the log)
- [x] `./homebase disaggregate` (`--backtest` only reports, writes nothing)
- [ ] Tests with synthetic data (block sums exact, byte-identical
      output on a second run, DST switch days), README

Done when the backtest error is acceptable and the source files are unchanged.

### Step 2: Fusion logic (pure Python, no HA)

- [ ] `fusion.py`: hourly series per source in, fused hourly series out (value, source per hour,
      running sum)
- [ ] Portal hours count only with all 4 quarter hours present
- [ ] RF hour valid only if plausible: not negative, below a ceiling, not spanning a gap
- [ ] Priority: portal measured > RF valid > portal substitute > reconstructed
- [ ] Running sums in integer Wh, no float drift over 30k hours
- [ ] Check whether the portal offers meter counter readings; if so, compare them with the RF counter
- [ ] Validation: RF vs portal per hour and per day, implausible RF hours stay empty until the
      portal fills them
- [ ] Tests for every rule, dry run with the CSV files plus an RF export from HA

### Step 3: HA import spike (script from the Mac, test data only)

- [ ] Script against the HA websocket with a long-lived token from `.env`: import one week as
      `linznetz:test_grid` (`recorder/import_statistics`), read RF statistics back
      (`recorder/statistics_during_period`)
- [ ] Check in a statistics graph card (the Energy tab stays untouched)
- [ ] Confirm the payload format of the installed HA version, re-import overwrites, deletion via
      Developer tools > Statistics works
- [ ] Delete the test statistic

### Step 4: Add-on skeleton

- [ ] `repository.yaml` + add-on folder in this repo, reusing `databot/`
- [ ] `config.yaml` with `homeassistant_api: true`, options: portal login (password field),
      meter ID, RF entity, site name
- [ ] Multi-arch image (at least `aarch64`) built by GitHub Actions
- [ ] CSV storage in the add-on config folder (part of HA backups)
- [ ] Portal fetch twice a day, import of `…_grid_energy_portal`
- [ ] Login failure is its own state: HA notification, the add-on keeps running on RF
- [ ] Portal client: an expired session is retried with a fresh login (today `_ajax` raises
      `LoginError('session expired')` and `fetch_csv` gives up as if the password were wrong);
      `LoginError` stays for rejected credentials only
- [ ] Portal client: tests against recorded, anonymized portal responses of each step of the
      form flow, so layout changes of the portal show up in the tests
- [ ] Installs from the repo URL on HA OS, survives a restart, no credentials in the logs

### Step 5: Load the history once

- [ ] Existing CSV files (readings and `reconstructed_15min`) copied to `/share` once, the add-on
      merges them into its storage (idempotent)
- [ ] `…_grid_energy_portal` and the reconstructed history reach back to March 2023

### Step 6: Fusion live, running alongside the current setup

- [ ] Hourly (a few minutes past the hour): read RF statistics, rebuild and import
      `…_grid_energy_fused`
- [ ] Deviation sensor over MQTT, notification above a threshold
- [ ] The portal delivers 1 to 2 days late: RF covers that span, the deviation check waits for it
- [ ] About 2 weeks in parallel while the Energy tab still uses the RF sensor, compare daily

### Step 7: Switch the Energy tab

- [ ] HA backup, then change the grid source to `…_grid_energy_fused` (reversible, the old
      statistics stay)
- [ ] Optional: `…_grid_power_fused` for the power chart
- [ ] Energy tab shows March 2023 to now, daily totals match the portal

### Step 8: Docs and cleanup

- [ ] Add-on README with the prerequisites: quarter-hour analysis ("Viertelstundenauswertung")
      enabled in the portal, data arrives 1 to 2 days late
- [ ] Add-on README, homebase README, `smartmeter/` docs (guard sensor possibly unnecessary)
- [ ] Decide whether the Docker databot keeps writing the CSV archive outside HA

## Related projects and what we take from them

We stay with the tools of this repo (portal client, CSV files, add-on); these projects are
references only. All three are HACS custom integrations, none fuses portal and live data.

- [DarkC35/ha_linznetz](https://github.com/DarkC35/ha_linznetz) (LINZ NETZ): imports the
  quarter-hour CSV (downloaded by hand or from the daily e-mail report) into the statistics of a
  placeholder sensor and patches the running sum when older values arrive. Its README admits the
  sums can get corrupted and recommends a full re-import. Taken: rebuild the fused series
  completely on every run instead of patching sums; the daily e-mail report ("Tagesbericht
  Viertelstundenverbrauch") as a possible fallback source if the portal login breaks; hours are
  only imported with all four quarter hours, and the autumn DST hour needs care (our parser
  already resolves it, see `test_parse_csv_autumn_dst_switch`).
- [superbox-dev/netzooe_eservice](https://github.com/superbox-dev/netzooe_eservice) and
  [VarChar42/hassio-netzooe-eservice](https://github.com/VarChar42/hassio-netzooe-eservice)
  (Netz OÖ, a different grid operator with a JSON API): sensors for meter reading and
  daily/weekly consumption, no hourly history. Taken: a portal login failure should surface as a
  clear "re-authenticate" state (they use HA's auth-failed flow), for the add-on that means a
  persistent notification and an error sensor instead of silent gaps.

## Related projects

All three are HACS custom integrations, none fuses portal history with live readings. We keep our
own approach and take over what improves it (already reflected in the steps above).

- [DarkC35/ha_linznetz](https://github.com/DarkC35/ha_linznetz) (LINZ NETZ): imports the
  quarter-hour CSV (e-mail report or manual download) into the statistics of a placeholder sensor
  and patches the running sum on overlaps; its README advises a full re-import when the sums get
  corrupted. Taken over: full rebuild instead of patching, exact (integer) sums, only complete
  hours, the e-mail report as fallback source, the 1 to 2 day delay of the portal. Its DST marker
  (start == end on the repeated autumn hour) is already handled by `linznetz.py`.
- [superbox-dev/netzooe_eservice](https://github.com/superbox-dev/netzooe_eservice) and
  [VarChar42/hassio-netzooe-eservice](https://github.com/VarChar42/hassio-netzooe-eservice)
  (Netz OÖ, a different portal with a JSON API): sensors only, no history. Taken over: the portal
  client stays independent of HA, a login failure is its own state, meter counter readings as a
  validation source.

## Risks

- Portal login changes (for example 2FA): the add-on must report it clearly and fall back to
  RF only. Second data source as fallback: the daily e-mail report "Tagesbericht
  Viertelstundenverbrauch" (S/MIME encrypted, see ha_linznetz).
- The recorder websocket API changes between HA releases: the step 3 script doubles as a check
  after major updates.
- Personal data in the public repo: check the whole branch diff before every PR.

## Log

- 2026-10-14: backtest of step 1 on real data (hourly error as share of the consumption, profile
  from 2023-09 to 2026-08 without the test season). All profile variants land at 31 to 33 %,
  the uniform split at about 36 %. The variants differ by less than the noise between the two
  test seasons; median "season x day type" is best or tied best in both (32.3 % and 30.9 %).
  Conclusion: a profile improves the hourly shape only modestly, single hours are dominated by
  random appliance use. Daily and 6h totals stay exact either way.

  | Variant                   | test Mar-Aug 2024 | test Mar-Aug 2025 |
  | ------------------------- | ----------------- | ----------------- |
  | uniform split             | 36.4 %            | not run           |
  | mean, month x day type    | 33.1 %            | 31.8 %            |
  | mean, day type only       | 32.5 %            | 30.9 %            |
  | median, month x day type  | 32.4 %            | 31.1 %            |
  | median, season x day type | 32.3 %            | 30.9 %            |
  | mean, month x weekday     | 34.9 %            | 32.9 %            |

- 2026-10-14: portal clients of the related projects compared with `linznetz.py`; learnings
  added to steps 4 and 8.
- 2026-10-14: related projects reviewed, learnings added to steps 2, 4 and 6.
- 2026-10-14: plan written, step 0 done except the backup, step 1 started (branch
  `fusion-disaggregate`).
