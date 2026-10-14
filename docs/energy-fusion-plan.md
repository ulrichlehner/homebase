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

- [ ] `disaggregate.py`: typical profile per quarter hour of the day, by month and day type
      (workday, weekend or Austrian public holiday), from a fixed window of measured portal data
- [ ] Each 6h block is split by the profile weights of its quarter hours, the block total stays
      exact (rounding with largest remainder), output `<meterId>_reconstructed_15min.csv` with
      `method=profile` and a note naming the profile
- [ ] Backtest: measured days summed into 6h blocks, split again, compared with the real hourly
      values; reports the error next to a plain uniform split
- [ ] `merge` uses the 15-minute file instead of the 6h blocks when it exists
- [ ] `./homebase disaggregate`, tests with synthetic data (block sums exact, byte-identical
      output on a second run, DST switch days), README

Done when the backtest error is acceptable and the source files are unchanged.

### Step 2: Fusion logic (pure Python, no HA)

- [ ] `fusion.py`: hourly series per source in, fused hourly series out (value, source per hour,
      running sum)
- [ ] Portal hours count only with all 4 quarter hours present
- [ ] RF hour valid only if plausible: not negative, below a ceiling, not spanning a gap
- [ ] Priority: portal measured > RF valid > portal substitute > reconstructed
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
- [ ] Installs from the repo URL on HA OS, survives a restart, no credentials in the logs

### Step 5: Load the history once

- [ ] Existing CSV files (readings and `reconstructed_15min`) copied to `/share` once, the add-on
      merges them into its storage (idempotent)
- [ ] `…_grid_energy_portal` and the reconstructed history reach back to March 2023

### Step 6: Fusion live, running alongside the current setup

- [ ] Hourly (a few minutes past the hour): read RF statistics, rebuild and import
      `…_grid_energy_fused`
- [ ] Deviation sensor over MQTT, notification above a threshold
- [ ] About 2 weeks in parallel while the Energy tab still uses the RF sensor, compare daily

### Step 7: Switch the Energy tab

- [ ] HA backup, then change the grid source to `…_grid_energy_fused` (reversible, the old
      statistics stay)
- [ ] Optional: `…_grid_power_fused` for the power chart
- [ ] Energy tab shows March 2023 to now, daily totals match the portal

### Step 8: Docs and cleanup

- [ ] Add-on README, homebase README, `smartmeter/` docs (guard sensor possibly unnecessary)
- [ ] Decide whether the Docker databot keeps writing the CSV archive outside HA

## Risks

- Portal login changes (for example 2FA): the add-on must report it clearly and fall back to
  RF only.
- The recorder websocket API changes between HA releases: the step 3 script doubles as a check
  after major updates.
- Personal data in the public repo: check the whole branch diff before every PR.

## Log

- 2026-10-14: plan written, step 0 done except the backup, step 1 started (branch
  `fusion-disaggregate`).
