# Homebase

> Not affiliated with LINZ NETZ GmbH. The tools and the Home Assistant add-on use the public LINZ NETZ
> customer portal with your own login, the same way you would in the browser.

Home energy monitoring: fetches quarter-hour smart meter readings from the
[LINZ NETZ service portal](https://www.linznetz.at/portal/de/home/online_services/serviceportal)
into CSV files and renders weekly consumption charts.

## How it works

`lib/homebase/portal.py` talks to the portal ("Verbrauchsdateninformation") over plain HTTP: it logs in via
the Keycloak SSO, replays the JSF form posts that select *Viertelstundenwerte* and the date range, and
downloads the CSV exports for *Energiemenge in kWh* and *Leistung in kW*. No browser, no database.

Everything lives in one data folder (`DATA_DIR`, see below):

| File                             | Content                                                                 |
| -------------------------------- | ----------------------------------------------------------------------- |
| `<meterId>_readings.csv`         | every measured quarter hour: local + UTC start, kWh, kW, substitute flag |
| `<meterId>_reconstructed_6h.csv` | 6-hour values reconstructed from old chart PNGs (optional, see below)   |
| `<meterId>_reconstructed_15min.csv` | the 6-hour values split into quarter hours by a typical profile        |
| `<meterId>_all.csv`              | both combined into one contiguous timeline with `source`/`method`/`note` |
| `csv/`                           | the raw portal exports, exactly as downloaded                            |
| `*.png`                          | the rendered weekly charts and `current.png`, all at the top level       |
| `original_charts/`               | backup: the charts as they were before `render --all` replaced them; never delete it |

`databot csv` keeps the readings file current and rewrites the unified file; `render.py` draws the
charts from the unified file. Both run on demand; the regular fetching for Home Assistant is done by
the add-on in `linznetz_energy/`.

Prerequisite: the quarter-hour analysis ("Viertelstundenauswertung") must be enabled for the meter
in the portal. The portal shows the values of a day one to two days later.

## Run

Everything runs in Docker through the `./homebase` wrapper, nothing else needs to be installed. The
wrapper builds the image locally and rebuilds it automatically whenever the sources in `lib/` or
`databot/` change.

```bash
./homebase csv            # fetch new readings from the portal into the CSV files
./homebase render         # render the current chart, `render --archive` for finished weeks
./homebase render --all   # re-render ALL weekly charts and the current one from the CSV data
./homebase check          # report incomplete days in the readings CSV
./homebase reconstruct    # reconstruct 6h values from archived chart PNGs (see below)
./homebase disaggregate   # split the reconstructed 6h values into quarter hours (see below)
./homebase fuse           # one hourly series from portal, RF and reconstruction (see below)
./homebase test           # run the unit tests
```

`DATA_DIR` in `.env` selects the folder that holds the CSV files and the charts. Point it at an
iCloud folder so nothing is lost when the machine dies; the default is `./data/databot`. The first
`csv` run downloads the complete history the portal offers (about three years), later runs take a
few seconds.

The portal only keeps about 36 months and drops the oldest month every month, so older readings
exist only in `<meterId>_readings.csv`. Never delete it: if the folder was emptied, restore the
CSV files (and `original_charts/`) from a backup before running `csv`, otherwise the first run
starts a new file with whatever the portal still has.

## Commands

All commands read the configuration from `.env` (see [App setup](#app)).

### Readings CSV

`./homebase csv` keeps `<meterId>_readings.csv` up to date straight from the portal. Run it whenever
you like: the first run downloads the complete history, every later run re-fetches the last 7 days
(`--overlap`, the portal corrects values retroactively) and appends what is new. Each run also checks
every day in the file for the expected number of quarter-hour intervals and re-fetches incomplete
days, looks for data older than the first row, and finally rewrites the unified file, so a damaged
or partial file heals itself. Malformed lines are skipped with a warning. Do not edit the files with
Excel, it rewrites the timestamps. Writes are atomic, an interrupted run leaves the previous file. The file has one row per quarter hour with local and UTC start
time, kWh, kW and a substitute-value flag (`<DATA_DIR>/<meterId>_readings.csv`). The raw portal
exports of every run are kept in `<DATA_DIR>/csv/`.

### Charts

`./homebase render` draws `current.png` (the running week plus the two before) from `<meterId>_all.csv`.
`./homebase render --archive` adds the chart of each finished week as `<meterId>_<monday>-<sunday>.png`.

`./homebase render --all` re-renders **every** weekly chart from the first week with data to the last
finished week, plus `current.png`, e.g. after the renderer changed or after gaps in the data were
filled. It takes about a minute. All charts live at the top level of `DATA_DIR` (an `archive/`
subfolder is still recognized, but not needed). Before a chart is replaced for the first time, the
original is kept under `DATA_DIR/original_charts/` (same file name) and is never overwritten again.
`original_charts/` is the backup of the old charts and the input of the reconstruction below: never
delete it; `reconstruct` reads it in preference to the re-rendered charts. Files in it whose name has
a suffix (for example `_rendered-2026-09-25`) are kept but ignored.

### Reconstructing lost history from the chart PNGs

The portal only keeps about 36 months. Readings before 2023-09-01 were only in the original InfluxDB,
which was lost, but the weekly charts it rendered survive. `./homebase reconstruct` reads every
chart PNG in `DATA_DIR` (or `original_charts/` once it exists) and writes `<meterId>_reconstructed_6h.csv` with the four 6-hour sums per
day for the period before the portal readings start. It is clearly marked as reconstructed:

- `source` is always `chart_ocr`, `method` says how the value was obtained (`label`: printed value
  read by OCR and verified against the bar height; `sum`: derived from the printed daily total;
  `height`: estimated from the bar height only), and `note` explains it in words.
- The blocks run from 00:15 to 06:15, 06:15 to 12:15, and so on, because the charts were rendered that
  way; `start_local` / `end_local` state it explicitly. Weeks containing a DST switch are flagged.
- Values are 6-hour sums, not quarter-hour readings. Days that were missing in the old database are
  missing here too.

The script validates itself against the real readings where charts and portal data overlap (from
September 2023 on) and logs the agreement.

### Splitting the 6-hour values into quarter hours

Home Assistant keeps hourly statistics, and the reconstructed blocks run from 00:15 to 06:15 and so
on, so they do not even line up with full hours. `./homebase disaggregate` splits every block into
its quarter hours and writes `<meterId>_reconstructed_15min.csv`; `reconstruct` runs it
automatically.

- The weights come from a typical profile of the household: the median kWh of each quarter hour of
  the day per meteorological season and day type (workday, or weekend and Austrian public
  holiday), taken from the measured portal readings of a fixed window (2023-09-01 to 2026-08-31,
  `--profile-from` / `--profile-to`). The window is fixed so new readings never change the
  estimates; the same inputs give a byte-identical file.
- Every block keeps its exact total (rounded to Wh with the largest remainder), so the 6-hour and
  daily sums are exactly the reconstructed ones. Only the shape inside a block is estimated.
- Rows keep `source=chart_ocr`, get `method=profile`, and the note names the block and the profile.
- Each run first backtests the method: measured days from a test season (default March to August
  2024, `--backtest-from` / `--backtest-to`, left out of the profile) are summed into the same
  blocks, split again and compared with the real hourly values. With the default profile the
  hourly error is about 31 to 32 % of the consumption, against 36 % for a plain uniform split:
  single hours are dominated by random appliance use, the profile gives a believable daily shape,
  not the real peaks. `./homebase disaggregate --backtest` only reports, it writes nothing.

### Fusing portal, radio and reconstructed readings

`./homebase fuse` writes `<meterId>_fused_1h.csv`, one row per hour with the best source
available. The radio (RF) readings of the meter in Home Assistant are the primary source; the
portal verifies them and fills their gaps (fully measured hours first, then substitute values),
the reconstructed quarter hours cover 2023. Columns:
`start_local;start_utc;kwh;source;sum_kwh;portal_kwh;flag`: the running sum is computed in whole
Wh, `portal_kwh` is the complete portal value of the hour (if any) and `flag` is `deviation` when
an RF hour differs from it by more than 2 % (at least 20 Wh); the RF value is kept. It prepares
the import into the Home Assistant Energy tab, see `docs/energy-fusion-plan.md`.

RF readings come from a Home Assistant history download of the energy counter sensor (History,
pick the sensor and the period, "Download data"): `./homebase fuse --rf ~/Downloads/history.csv`.
The counter is cleaned first: misread phases (wrong scale, near-zero values, spikes) break the
plausible counter and are dropped, and an hour gets an RF value only if accepted readings at most
60 minutes apart surround both of its ends. The run also compares RF with the portal per hour
and per day and warns about days that differ by more than 2 %.

### One file with everything

`<DATA_DIR>/<meterId>_all.csv` is written after every `csv` and `reconstruct` run (or with
`./homebase merge`). It contains the reconstructed history followed by every measured quarter hour,
contiguous and without double counting. The reconstructed quarter hours are used when
`<meterId>_reconstructed_15min.csv` exists (rows from the first measured reading on are dropped),
otherwise the 6-hour blocks (the last one is trimmed to the first measured reading). Columns:
`start_local;end_local;kwh;kw;source;method;note`, where `source` is `chart_ocr` or `portal`,
`method` is `label`, `manual`, `sum`, `height`, `profile`, `measured` or `substitute`, and `note`
explains it in words. All files are semicolon-separated and contain no commas.

Labels the OCR cannot verify (typically small bars whose label overlaps the printed daily total) can
be read by a human and recorded in `<DATA_DIR>/<meterId>_reconstructed_corrections.csv`
(`start_local;kwh;read_by;note`, `start_local` as in the output file). The script picks that file up
automatically, the affected rows get `method=manual`. To find candidates, run `./homebase reconstruct -d`
and look for "no readable label", or check the `method` column of the output.

`update` re-fetches the last 7 days (`--overlap`) because the portal corrects values retroactively.
Writes are idempotent, re-loading a range simply overwrites the same points. Use `--chunk-days` (default 366)
to change how many days are requested from the portal per request, and `-d` for debug logging.

## Releases

The Home Assistant add-on in `linznetz_energy/` is versioned with [release-please](https://github.com/googleapis/release-please)
from [conventional commits](https://www.conventionalcommits.org/): `fix:` gives a patch, `feat:` a minor
and `feat!:` or a `BREAKING CHANGE:` footer a major release; `docs:`, `chore:`, `test:` and `refactor:`
give none. It starts at 0.1.0 while the add-on is incomplete; before 1.0.0 a breaking change only
gives a minor release, and 1.0.0 is set deliberately once the fused statistic drives the Energy tab. On every push to `main` release-please opens or updates a release PR with the next version
in `linznetz_energy/config.yaml` and the new entries in `linznetz_energy/CHANGELOG.md`. Merging it
creates the tag and the GitHub release, and the image workflow builds the images of that version.
Version images are never overwritten: HA offers the update as soon as the release PR is merged and
the images are built (a few minutes). The only exception is a manual run of the workflow
`addon-image.yml` with `force`, for example to replace an image built from a branch by mistake.

### Add-on icon and logo

`linznetz_energy/icon.png` and `logo.png` are rendered from the SVG sources in
`linznetz_energy/assets/` (`icon.svg`; `logo.svg` embeds it and adds the name). Change the SVG, then
run `./homebase assets` and commit the PNGs too; `./homebase assets --check` (and the workflow
`addon-assets.yml`) fails when a PNG no longer matches its SVG.

## Repository layout

| Folder              | Content                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------- |
| `lib/homebase/`     | the shared library (Python package `homebase`), no command line, no add-on specifics: `portal` (LINZ NETZ client), `readings` (the CSV archive), `disaggregate`, `fusion`, `rfarchive` (the archive of the radio readings), `hass` (Home Assistant websocket client) |
| `databot/`          | the command line tools for Docker on the Mac: `databot.py` (`csv`, `merge`, `check`, `disaggregate`, `fuse`), `render.py` (charts), `reconstruct.py` (chart OCR), `ha_spike.py` (HA import test) |
| `linznetz_energy/`  | the Home Assistant add-on: `app/main.py` (main loop), `app/web.py` (its page in HA), `config.yaml`, docs, assets |
| `smartmeter/`       | the wM-Bus driver and MQTT discovery for the RF readings of the meter                    |

Both images install the library from `lib/` (`databot/Dockerfile` and `linznetz_energy/Dockerfile`,
built from the repository root). Each folder has its tests next to it (`lib/tests/`,
`databot/tests/`, `linznetz_energy/tests/`); `pytest.ini` puts the three source folders on the import
path.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e lib -r databot/requirements-dev.txt
.venv/bin/python -m pytest
EXPORT_DIR=data/databot .venv/bin/python databot/databot.py -c .env -d csv
EXPORT_DIR=data/databot LOGLEVEL=DEBUG .venv/bin/python databot/render.py
```

Without a local Python, `./homebase test` runs all tests inside the container against the working
tree, and `./homebase shell` opens a shell there.

## Setup

Requires Docker (Docker Desktop on the Mac).

### App

Clone this repo and create a `./.env` with the following content, then run `./homebase csv`:

```bash
USERNAME=<LINZ NETZ portal login>
PASSWORD=<LINZ NETZ portal password>
METER_ID=<your metering point number (Zählpunktnummer, 33 characters, not the meter number), used as file name prefix>
TZ=Europe/Vienna
DATA_DIR="/Users/<you>/Library/Mobile Documents/com~apple~CloudDocs/<folder>"  # optional, default ./data/databot
# optional, only for `./homebase ha-spike` (Home Assistant statistics import test)
HA_URL=http://homeassistant.local:8123
HA_TOKEN=<long-lived access token from your HA profile, Security tab>
HA_RF_ENTITY=sensor.<place>_grid_total_energy_consumption
```
