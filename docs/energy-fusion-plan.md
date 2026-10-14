# Plan: portal history + live RF readings in the Home Assistant Energy tab

Goal: one view in the HA Energy tab with the best possible data quality, from the first
reconstructed day (March 2023) up to the current hour:

1. long history (portal readings, the CSV archive and the reconstructed chart history),
2. gaps and glitches of the wM-Bus radio readings (RF) filled by the portal values,
3. RF readings validated against the portal.

Constraints: runs inside Home Assistant OS as a normal add-on from a custom add-on repository
(no HACS, no custom integration, HA not exposed to the internet). As native to HA as possible:
feedback through notifications, status for automations through regular entities (MQTT discovery).
HA has no native file upload for add-ons (the configuration tab has no file field, the media upload
only takes images, audio and video), so the add-on has one small page through Ingress, HA's own
mechanism for add-on pages (HA login, opens inside HA, HA look): status and history upload. The
user does not want Samba as the way in. Every derived file is
non-destructive and repeatable: inputs are never modified, the same inputs give the same output.

## Design in short

The add-on writes **external statistics** (like the Opower integration does). It never writes
into the statistics of the RF sensor, whose running `sum` would have to be shifted for every
filled gap and which the recorder keeps extending every hour.

Every hour in the fused series takes the best source available:

| Period / case                                   | Source                                        |
| ----------------------------------------------- | --------------------------------------------- |
| March to August 2023                            | 6h chart blocks split by a typical profile    |
| September 2023 until RF readings exist          | portal, measured quarter hours                |
| hours with a valid RF reading                   | RF (primary, decided 2026-10-14)              |
| RF missing or invalid (gap, misread phase)      | portal, once it delivers (1 to 2 days later)  |

RF is the primary source; the portal verifies it and fills its gaps. An hour where RF and the
portal deviate beyond the tolerance keeps the RF value, is flagged, and raises a notification.

The fused series is rebuilt completely on every run (about 30k hourly rows), so a late portal
value or a corrected rule fixes the past automatically. Data quality per hour stays in the CSV
files, HA statistics have no field for it.

## Names

| What                         | ID                                         | Notes                                     |
| ---------------------------- | ------------------------------------------ | ----------------------------------------- |
| RF sensors (existing)        | `sensor.<place>_grid_*`                    | unchanged, stock amiplus entity names     |
| fused, used in the Energy tab | `linznetz_energy:<place>_grid_consumption`        | external statistic, kWh, `sum`, "<Place> grid consumption" |
| portal only, for checks      | `linznetz_energy:<place>_grid_consumption_portal` | external statistic, kWh, `sum`, "<Place> grid consumption (portal)" |
| feed-in (PV site, later)     | `linznetz_energy:<place>_grid_return`             | external statistic, kWh, `sum`            |
| fused power (optional)       | `linznetz_energy:<place>_grid_power`              | external statistic, kW, `mean`/`min`/`max` |
| RF vs portal deviation       | `sensor.<place>_grid_rf_deviation`         | MQTT discovery, published by the add-on   |

The statistic domain is the add-on slug `linznetz_energy` (unique: `linznetz` is also the domain
of the HACS integration ha_linznetz). The main statistic has the plain name, matching HA's term
"grid consumption" and the RF sensor (`total_energy_consumption`); "fused" is an implementation
detail. Add-on options: `portal_username`, `portal_password`, `metering_point` (the
Zählpunktnummer, not the meter number), `site`, `fetch_interval_hours`.

The RF entities keep their IDs: renaming them gains nothing (the `linznetz_energy:` prefix already
separates the derived statistics) and every rename risks the existing history. The display names
of the external statistics say `(portal)` / `(fused)` so the Energy tab never shows two entries
that look the same. Everything takes the site as a parameter, a second site (with PV feed-in)
can be added later.

## Steps and progress

Each step ends with something that can be checked before the next one starts. Steps 0 to 5 were
done in one branch (`fusion-disaggregate`, PR #13, step 5 pulled in so the first install starts with
the full history), merged so the add-on installs from the plain repository URL. Steps 6 to 8 follow
in one new branch and PR. Nothing touches the live
HA setup before step 7, except the separate `…_grid_consumption_portal` statistic.

### Step 0: Prerequisites

- [x] HA host: Raspberry Pi 5, `aarch64` (add-on image must be built for it)
- [x] RF entity IDs: `sensor.<place>_grid_*`
- [x] Full HA backup before the first statistics import (step 3): "Before HA Energy Add-on", 2026-10-14

### Step 1: Split the 6h chart blocks into quarter hours (databot, offline)

- [x] `disaggregate.py`: typical profile per quarter hour of the day (median per season and day
      type: workday, or weekend and Austrian public holiday), from a fixed window of measured
      portal data (2023-09-01 to 2026-08-31)
- [x] Each 6h block is split by the profile weights of its quarter hours, the block total stays
      exact (rounding with largest remainder), output `<meterId>_reconstructed_15min.csv` with
      `method=profile` and a note naming the profile
- [x] Backtest: measured days summed into 6h blocks, split again, compared with the real hourly
      values; reports the error next to a plain uniform split
- [x] `merge` uses the 15-minute file instead of the 6h blocks when it exists; `reconstruct`
      runs `disaggregate` before `merge`, `merge` warns when the split is older than the blocks
- [x] Decide the profile variant: median, season x day type (see the log)
- [x] `./homebase disaggregate` (`--backtest` only reports, writes nothing)
- [x] Tests with synthetic data (block sums exact, byte-identical output on a second run, inputs
      untouched, DST switch days, holidays, backtest, unified file), README

Done when the backtest error is acceptable and the source files are unchanged.

### Step 2: Fusion logic (pure Python, no HA)

- [x] `fusion.py`: portal quarter hours, RF counter and reconstructed quarter hours in, fused
      hourly series out (value, source per hour, running sum), `<meterId>_fused_1h.csv`
- [x] RF from the raw counter history (HA history download), not from HA's hourly statistics:
      those already contain the spikes of misread telegrams
- [x] RF cleaning: cut the counter into segments where it falls or rises faster than `max_kw`
      (20 kW), trust the longest segment, chain the others that continue it plausibly, drop the
      rest (wrong scale, 0.008 kWh, spikes, a bad first reading); hourly values interpolated
      between accepted readings at most 60 minutes apart, otherwise no RF value for that hour
- [x] Portal hours count as measured only with all 4 quarter hours present
- [x] Priority: portal measured > RF valid > portal substitute > reconstructed > portal partial;
      hours without any source are left out, the sum continues
- [x] Running sums in integer Wh, no float drift
- [x] Validation: RF vs portal per hour and per complete local day (tolerance 2 %, at least
      50 Wh), logged by `./homebase fuse --rf <history.csv>`
- [x] Tests for every rule (synthetic data)
- [x] Dry run without RF: 31631 hours (portal and reconstructed), no gaps
- [x] Dry run with a real RF history download of the energy counter (12.10. 13:18 to 14.10.):
      35 hours against the portal, mean 1 Wh per hour, +0.01 %, 13.10. complete and within the
      tolerance; today's hours filled from RF
- [ ] Dry run over the misread phases of 9.10. and 11.10. (the history download only starts at
      12.10. 13:18; check whether HA still has older raw states, they are purged around
      19.10./21.10. unless `purge_keep_days` is raised)
- [x] Cross-check with the RF power sensor (integrated, 12.10. 13:18 to 14.10.): 35 complete
      hours -0.33 % against the portal, mean 3 Wh per hour, 13.10. 9.718 vs 9.772 kWh
- [ ] Check whether the portal offers meter counter readings; if so, compare them with the RF
      counter (optional, the hourly validation already covers the drift)

### Step 3: HA import spike (script from the Mac, test data only)

- [x] `lib/homebase/hass.py` (small websocket client, reused by the add-on) and `./homebase ha-spike
      info | import | clear` (`databot/ha_spike.py`), tested without HA; the metadata fields newer
      HA versions want (`mean_type`, `unit_class`) are discovered and logged
- [x] Run against HA 2026.10.1: one week (168 hours, 70.167 kWh) imported as `linznetz:test_grid`,
      read back exactly, re-import overwrites (no duplicates), RF counter history readable
      (`history/history_during_period`, about 4700 states per day)
- [x] Payload: the minimal metadata (`source`, `statistic_id`, `name`, `unit_of_measurement`,
      `has_mean`, `has_sum`) is accepted, HA adds `mean_type` and `unit_class` itself
- [x] The recorder imports asynchronously: read back only after polling (about 0.6 s)
- [x] Visual check in a statistics graph card: 13.10. shows 9.77 kWh like the Energy tab, and
      9.10./11.10. are plausible (about 11 and 10 kWh) where the RF statistics have the spikes
- [x] Delete the test statistic (`./homebase ha-spike clear`); the token stays until step 8 for
      read-only checks from the Mac (the add-on uses the Supervisor token)

### Step 4: Add-on skeleton

- [x] `repository.yaml` + add-on folder `linznetz_energy/` in this repo, reusing `databot/`
      (now the library `lib/homebase/`); main loop `linznetz_energy/app/main.py`
- [x] `config.yaml` with `homeassistant_api: true` (websocket through the Supervisor, no token),
      options: portal login (password field), meter ID, site name, `fetch_interval_hours`
      (English and German translations); the time zone
      comes from HA; the RF entity follows in step 6
- [x] Image per architecture (`aarch64`, `amd64`) built by GitHub Actions
      (`.github/workflows/addon-image.yml`), `ghcr.io/ulrichlehner/homebase-linznetz-energy-<arch>`
- [x] CSV storage in the add-on config folder (`addon_config`, part of HA backups)
- [x] Portal fetch every 12 hours, import of `…_grid_consumption_portal` every hour (complete rebuild,
      in chunks of 5000 rows)
- [x] Login failure is its own state: HA notification, retry after the interval, the statistic is
      still imported from the CSV files; the notification disappears after the next good login
- [x] Portal client: an expired session is retried with a fresh login (`SessionExpiredError`)
- [ ] Portal client: tests against recorded, anonymized portal responses of each step of the
      form flow, so layout changes of the portal show up in the tests
- [x] Tested from the Mac with the add-on image against HA: 27337 hours (6626.673 kWh) imported
      as `linznetz_energy:<place>_grid_consumption_portal`, portal login and the login-failure notification
      (created and dismissed again) work
- [x] Image published and public on ghcr.io (both architectures, version 0.1.0)
- [x] Versioning: starts at 0.1.0 (incomplete until step 7), release-please derives the next version from the
      conventional commits (release PR, tag, changelog); version images are immutable, `main`
      only builds a version that has no image yet, other branches push `dev`
- [x] Store presentation: `icon.png`, `logo.png` (rendered from the SVGs in `linznetz_energy/assets/`
      by `./homebase assets`, checked in CI; readable in light and dark mode), `CHANGELOG.md`,
      `stage: experimental`, minimum HA version 2026.10.0 (the tested one)
- [ ] Installs from the repo URL on HA OS (`https://github.com/ulrichlehner/homebase`, after the
      merge of #13), survives a restart, no credentials in the logs

### Step 5: Load the history once

The portal keeps about 36 months: on 2026-10-14 it starts at 2023-10-01, September 2023 exists only
in the CSV archive. Without this step the add-on's statistic starts at whatever the portal still has.

- [x] `import/` folder in the add-on config folder (reachable over Samba, created on start; the
      `share` mapping is gone): `<meterId>_readings.csv` is merged row by row (rows the add-on
      already has win), `<meterId>_reconstructed_15min.csv` replaces the previous one; processed
      files move to `import/done/`, rejected ones (other meter, wrong header, other file) to
      `import/failed/`; idempotent; HA notification with what was imported
- [x] First download announced and reported with a notification (count and range; failure and
      "no data" too)
- [x] New statistic `linznetz_energy:<site>_grid_consumption` (portal, then reconstructed; RF in step 6),
      `…_grid_consumption_portal` stays portal only
- [x] Tested end to end with the add-on image against HA: 109348 readings and 17180
      reconstructed quarter hours imported; `…_portal` 27337 hours from 2023-09-01 (6626.673
      kWh), `…_fused` 31631 hours from 2023-03-06 (7301.395 kWh)
- [x] Add-on page (Ingress, "Open Web UI"): status (state, runs, portal login, data ranges, both
      statistics with sources and deviating hours, last import) and the history upload, which
      imports right away; only reachable through the Ingress proxy; light and dark mode; tested
      locally against HA: first download, then upload added exactly 2880 readings (September
      2023) and the reconstruction
- [ ] On the installed add-on: upload both files on the add-on page

### Step 6: Fusion live, running alongside the current setup

- [ ] Hourly (a few minutes past the hour): read the RF counter history, rebuild and import
      `…_grid_consumption`
- [x] Priority in `fusion.py` changed to RF primary: RF valid > portal measured > portal
      substitute > reconstructed > portal partial; deviating hours (more than 2 %, at least 20 Wh
      per hour) keep RF and are flagged in the fused CSV (`portal_kwh`, `flag`); dry run with the
      RF history read from HA: 53 RF hours, none flagged
- [ ] Deviating hours counted in the status entities and notified
- [ ] Archive the RF readings (energy counter and power) in the add-on storage, part of every HA
      backup: the recorder purges raw states after `purge_keep_days` (default 10), long-term
      statistics stay. Optionally raise `purge_keep_days` in HA (the user's decision, bigger
      database)
- [ ] Status entities over MQTT discovery (device "LINZ NETZ energy <site>"): last portal fetch,
      portal data until, fused data from/until, RF deviation; notification above a threshold
- [ ] The portal delivers 1 to 2 days late: RF covers that span, the deviation check waits for it
- [ ] About 2 weeks in parallel while the Energy tab still uses the RF sensor, compare daily

### Step 7: Switch the Energy tab

- [ ] HA backup, then change the grid source to `…_grid_consumption` (reversible, the old
      statistics stay)
- [ ] Optional: `…_grid_power_fused` for the power chart
- [ ] Energy tab shows March 2023 to now, daily totals match the portal

### Step 8: Docs and cleanup

- [ ] Revoke the long-lived HA token used by `ha-spike` (profile > Security)
- [x] After the first release (v0.1.0): remove `release-as` from `release-please-config.json`,
      otherwise every release PR stays at 0.1.0
- [ ] Release 1.0.0 deliberately (`release-as: 1.0.0`) once the Energy tab uses the fused statistic
- [ ] Remove `stage: experimental` from `linznetz_energy/config.yaml` once the Energy tab uses
      the fused statistic
- [ ] Add-on README with the prerequisites: quarter-hour analysis ("Viertelstundenauswertung")
      enabled in the portal, data arrives 1 to 2 days late (already in the homebase README)
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

- 2026-10-14: add-on texts (notifications, page) follow the language of HA (de/en), plainer
  wording; page with larger type and a drop zone for the upload; full metering point on the page;
  metering point keeps only letters and digits (invisible characters from copying).
- 2026-10-14: first install: starting before configuring failed with a regex message. Onboarding
  reworked (0.1.1): the add-on starts anyway and explains the missing settings, the metering point
  accepts spaces and lowercase, `site` defaults to `home`. HA now calls add-ons "Apps".
- 2026-10-14: naming review before the first install: statistic domain `linznetz_energy` (= add-on
  slug), `…_grid_consumption` (Energy dashboard) and `…_grid_consumption_portal`, options
  `portal_username`, `portal_password`, `metering_point`.
- 2026-10-14: HA cleaned up after the tests: the statistics `…_grid_consumption_portal` and
  `…_grid_consumption` written by the local add-on test runs deleted (the installed add-on creates
  them fresh), no `linznetz:` statistic, entity or notification left from the tests (since renamed to
  `linznetz_energy:<site>_grid_consumption` and `…_grid_consumption_portal`).
- 2026-10-14: code restructured for separation of concerns: `lib/homebase/` (library: portal,
  readings, disaggregate, fusion, hass), `databot/` (command line tools), `linznetz_energy/app/`
  (add-on: main, web). All derived files byte-identical before and after, add-on image tested
  against HA again.
- 2026-10-14: the user does not want Samba: history upload and status on an add-on page through
  Ingress (the configuration tab cannot take files).
- 2026-10-14: decisions: RF is the primary source (portal verifies and fills gaps, deviations
  are flagged and notified, RF stays); the first-download notifications are enough until the
  status entities of step 6.
- 2026-10-14: design rule: native HA only, no custom UI (Ingress); notifications for the first
  download added. - 2026-10-14: initial version 0.1.0 instead of 1.0.0 (the add-on is not complete); old 0.1.0/0.1.1
  images from early branch builds exist on ghcr, so 0.1.0 is rebuilt once after the merge
  (`addon-image.yml` with `force`).
- 2026-10-14: step 5 done ahead of the rest (in PR #13, so the first install starts with the full
  history): `import/` folder in the add-on, fused statistic with the reconstructed history.
- 2026-10-14: add-on versioning switched to 1.0.0 with release-please (standard-version is
  deprecated); maintainer email in `repository.yaml`.
- 2026-10-14: the portal now starts at 2023-10-01 (checked with a full download into a scratch
  folder): September 2023 is gone from the portal and exists only in the CSV archive. The user
  decided to merge after step 4 and install the add-on from `main`.
- 2026-10-14: step 3 done (visual check OK, test statistic deleted). Step 4 add-on written and
  tested from the Mac against HA; next: publish the image and install it on HA.
- 2026-10-14: step 3 run against HA 2026.10.1 (backup "Before HA Energy Add-on" taken first):
  import, read back and overwrite work; imports are asynchronous, the spike polls.
- 2026-10-14: user keeps the recorder at `purge_keep_days: 10` (raw states), the add-on archives
  the RF readings itself. Step 3 code written; waiting for the HA backup and `HA_URL`/`HA_TOKEN`.
- 2026-10-14: fusion dry run with the real RF energy counter: it matches the portal to about 1 Wh
  per hour (the portal values come from the same meter register). The counter has about 11
  silences of 30 to 52 minutes per two days while power keeps arriving every 5 s: the phases in
  which the driver discards the energy record. Interpolating across them costs nothing
  measurable, so the default `max_gap` is now 60 minutes (30 left 3 hours empty and no complete
  day to compare).
- 2026-10-14: RF power sensor history (5 s readings) integrated and compared with the portal:
  -0.33 % over 35 hours, mean 3 Wh per hour; the small shortfall comes from receive gaps up to
  110 s. The receiver and the portal agree. The energy counter export is still needed for the
  fusion dry run.
- 2026-10-14: step 2 implemented and tested (49 tests). Decision: RF comes from the raw counter
  history, not from HA's statistics, because the statistics already contain the misread spikes.
  Open: a dry run with a real HA history download of the RF energy sensor.
- 2026-10-14: step 1 done. Real run: 716 blocks (674.78 kWh) split into 17180 quarter hours, all
  complete blocks reproduced exactly, portal rows of the unified file unchanged. Backtest with the
  final profile: 32.3 % (test season 2024) and 30.9 % (2025) against 36.4 % uniform. Also fixed in
  this branch: an expired portal session is retried with a fresh login instead of failing like
  rejected credentials.
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
