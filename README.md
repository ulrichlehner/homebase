# Homebase

Raspberry Pi based home monitoring dashboard: fetches quarter-hour smart meter readings from the
[LINZ NETZ service portal](https://www.linznetz.at/portal/de/home/online_services/serviceportal),
stores them in InfluxDB and renders weekly consumption charts for a Kindle.

## How it works

`databot/linznetz.py` talks to the portal ("Verbrauchsdateninformation") over plain HTTP: it logs in via
the Keycloak SSO, replays the JSF form posts that select *Viertelstundenwerte* and the date range, and
downloads the CSV exports for *Energiemenge in kWh* and *Leistung in kW*. No browser is involved. The readings are written to InfluxDB with the schema

| Measurement          | Tag       | Fields                                  | Timestamp      |
| -------------------- | --------- | --------------------------------------- | -------------- |
| `meteredValues`      | `meterId` | `value` (kWh per 15 min), `substitute`  | interval start |
| `meteredPeakDemands` | `meterId` | `value` (kW, the portal's "Leistung")     | interval start |

`substitute` is `true` when the portal delivered an "Ersatzwert" instead of a metered value. Every raw
CSV that was fetched is archived under `data/databot/csv/` (`_kwh` and `_kw` files).

Cron jobs inside the container run `databot update` every 30 minutes (followed by rendering the chart),
`databot backfill` and `databot repair` once a day, and archive the weekly chart on Thursdays.

## Run

Everything runs in Docker through the `./homebase` wrapper, nothing else needs to be installed. The
wrapper rebuilds the image automatically whenever the sources in `databot/` change.

```bash
./homebase csv            # update the readings CSV from the portal (no database needed)
./homebase reconstruct    # reconstruct 6h values from the archived chart PNGs (see below)
./homebase up             # start InfluxDB + the cron jobs in the background
./homebase logs           # follow the container logs
./homebase databot check  # any databot command against the running stack
./homebase render         # render the Kindle chart
./homebase test           # run the unit tests
./homebase down           # stop the stack
```

`DATA_DIR` in `.env` selects the folder that holds the CSV files, the raw portal exports, the
charts and the reconstruction. Point it at an iCloud folder so nothing is lost when the machine
dies; the default is `./data/databot`.

On first start of the stack the database is empty, so the first `update` cron run turns into a full
backfill: it walks backwards a year per request until the portal has no more data. To start it right
away:

```bash
./homebase databot backfill
```

## Commands

All commands read the configuration from `.env` (see [App setup](#app)).

```bash
./homebase databot update            # fetch everything since the last stored reading (default)
./homebase databot backfill          # fetch older data until the portal has none left
./homebase databot backfill --until 01.01.2022 --limit 365
./homebase databot load --from 01.09.2025 --to 30.09.2025
./homebase databot check --days 60   # report days with missing quarter-hour intervals
./homebase databot repair            # ... and re-fetch them
./homebase databot export            # dump all readings to <DATA_DIR>/<meterId>_all.csv
./homebase render --archive
```

### Standalone CSV without the database

`./homebase csv` keeps a single CSV file up to date straight from the portal, no InfluxDB needed. Run
it whenever you like: the first run downloads the complete history, every later run re-fetches the
last 7 days and appends what is new. Each run also checks every day in the file for the expected
number of quarter-hour intervals and re-fetches incomplete days, and looks for data older than the
first row, so a damaged or partial file heals itself. Malformed lines are skipped with a warning.
Do not edit the file with Excel, it rewrites the timestamps. The file has one row per quarter hour with local and UTC start
time, kWh, kW and a substitute-value flag (`<DATA_DIR>/<meterId>_readings.csv`). The raw portal
exports of every run are kept in `<DATA_DIR>/csv/`.

### Reconstructing lost history from the chart PNGs

The portal only keeps about 36 months. Readings before 2023-09-01 were only in the original InfluxDB,
which was lost, but the weekly charts it rendered survive. `./homebase reconstruct` reads every
chart PNG in `DATA_DIR` and writes `<meterId>_reconstructed_6h.csv` with the four 6-hour sums per
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

### One file with everything

`<DATA_DIR>/<meterId>_all.csv` is written after every `csv` and `reconstruct` run (or with
`./homebase merge`). It contains the reconstructed 6-hour blocks followed by every measured quarter
hour, contiguous and without double counting (the last reconstructed block is trimmed to the first
measured reading). Columns: `start_local;end_local;kwh;kw;source;method;note`, where `source` is
`chart_ocr` or `portal`, `method` is `label`, `manual`, `sum`, `height`, `measured` or `substitute`,
and `note` explains it in words. All files are semicolon-separated and contain no commas.

Labels the OCR cannot verify (typically small bars whose label overlaps the printed daily total) can
be read by a human and recorded in `<DATA_DIR>/<meterId>_reconstructed_corrections.csv`
(`start_local;kwh;read_by;note`, `start_local` as in the output file). The script picks that file up
automatically, the affected rows get `method=manual`. To find candidates, run `./homebase reconstruct -d`
and look for "no readable label", or check the `method` column of the output.

`update` re-fetches the last 7 days (`--overlap`) because the portal corrects values retroactively.
Writes are idempotent, re-loading a range simply overwrites the same points. Use `--chunk-days` (default 366)
to change how many days are requested from the portal per request, and `-d` for debug logging.

## Develop

```bash
cd databot
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
INFLUX_URL=http://localhost:8086 .venv/bin/python databot.py -c ../.env -d check
LOGLEVEL=DEBUG INFLUX_URL=http://localhost:8086 .venv/bin/python render.py
```

For local runs InfluxDB must be reachable, e.g. by uncommenting the `ports` mapping in `docker-compose.yml`.
Without a local Python, `./homebase test` and `./homebase shell` run the same inside the container.

## Flux queries

Query all data:

```
from(bucket: "smartmeter")
  |> range(start: v.timeRangeStart, stop:v.timeRangeStop)
  |> filter(fn: (r) =>
    r._measurement == "meteredValues" and
    r._field == "value"
  )
```

Split in 6h time windows:

```
from(bucket: "smartmeter")
  |> range(start: v.timeRangeStart, stop:v.timeRangeStop)
  |> filter(fn: (r) =>
    r._measurement == "meteredValues" and
    r._field == "value"
  )
  |> aggregateWindow(every: 6h, offset: -12h, fn: mean)
```

## Setup

### Raspberry Pi

1. Install `Raspberry Pi OS Lite (64-Bit)`
2. Install `Docker` (see [Install Docker Engine on Debian](https://docs.docker.com/engine/install/debian/#install-using-the-convenience-script) for latest instructions, at this time the _Install using the convenience script_ section must be used for Raspberry Pi):

   ```bash
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh

   # Let the logged-in user use `docker` without `sudo`
   sudo usermod -aG docker ${USER}
   ```

3. (Optional) Make Raspian OS auto-update itself (confirm with `Yes` in the configuration)

   ```bash
   sudo apt-get install unattended-upgrades keychain # keychain for password protected ssh keys
   sudo dpkg-reconfigure -plow unattended-upgrades
   ```

4. Prepare Kindle (see below)

5. Setup USB network with Kindle (must be attached and prepared with `USBNetwork`)

   > TODO: Make`ifconfig usb0 ...` better since it's always set in the cron job, maybe [https://www.mobileread.com/forums/showthread.php?t=342904](https://www.mobileread.com/forums/showthread.php?t=342904) helps.

   On the raspberry add the following with `sudo nano /etc/dhcpcd.conf`:

   ```
   # Kindle via USBNetwork
   interface usb0
   static ip_address=192.168.15.244/24
   static routers=192.168.15.1
   ```

   Then `sudo reboot`.

   Generate a ssh keypair with

   ```
   ssh-keygen -t ed25519
   ```

   If you're using a password you can add it to `ssh-agent`:

   ```
   ssh-add ~/.ssh/id_ed25519
   ```

   This key must be added to the Kindle (see below).

   Start the interface with (actually only needed if you want to manually ssh into the Kindle. It's handled by the cron job)

   ```
   sudo ifconfig usb0 192.168.15.201
   ```

   Make the the kindle cron script executable with `chmod u+x <absolute path to clone git repot>/kindle_cron.sh` add following cronjob with `crontab -e`:

   ```
   */15 * * * * <absolute path to clone git repot>/kindle_cron.sh
   ```

### App

Clone this repo and create a `./.env` in the app folder with following content:

```bash
USERNAME=<LINZ NETZ portal login>
PASSWORD=<LINZ NETZ portal password>
METER_ID=<Zählpunktnummer, used as tag in the database>
DATA_DIR="/Users/<you>/Library/Mobile Documents/com~apple~CloudDocs/<folder>"  # optional, default ./data/databot
INFLUX_ORG=ulrichlehner
INFLUX_BUCKET=smartmeter
INFLUX_TOKEN=<genereate a secure token string>
TZ=Europe/Vienna
DOCKER_INFLUXDB_INIT_USERNAME=admin
DOCKER_INFLUXDB_INIT_PASSWORD=<generate a secure password>
DOCKER_INFLUXDB_INIT_ORG=ulrichlehner
DOCKER_INFLUXDB_INIT_BUCKET=smartmeter
DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=<genereate a secure token string>
```

The variables `INFLUX_BUCKET` and `DOCKER_INFLUXDB_INIT_BUCKET` must have the same value, as well as `INFLUX_TOKEN` = `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` and `INFLUX_ORG` = `DOCKER_INFLUXDB_INIT_ORG`.

### Kindle

#### Kindle Paperwhite 5th Generation (EY21) w/ firmware 5.6.1.1 jailbreak

**These instructions are only tested with the software and hardware version as above! If you have a different hardware / software setup you'll have to find instructions for your case.**

- The firmware _5.6.1.1_ has to be downgraded following these instructions [How to Downgrade PW 1 ca from 5.6.1.1 to 5.3.3 and maybe PW2](http://www.mobileread.mobi/forums/showthread.php?t=264432).

  - Keep WiFi off / enable airplane mode
  - Download the firmare _5.3.3_ [https://s3.amazonaws.com/G7G_FirmwareUpdates_WebDownloads/update_kindle_5.3.3.bin](https://s3.amazonaws.com/G7G_FirmwareUpdates_WebDownloads/update_kindle_5.3.3.bin) and upload it via USB to the Kindle's root folder (if it doesn't work you might try version _5.3.1_ via [http://kindle.s3.amazonaws.com/update_kindle_5.3.1.bin](http://kindle.s3.amazonaws.com/update_kindle_5.3.1.bin))
  - Push and hold power button the device restarts (you can release the button when the power LED turned off) and wait for the update (**keep the device connected to the computer until the update is done!**)

- Install the jailbreak as described in [Kindle Touch/PW1/PW2 5.0.x - 5.4.4.2 JailBreak. Plus FW 5.x USBNetwork](https://www.mobileread.com/forums/showthread.php?t=186645).

  - Download [https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/Touch/kindle-jailbreak-1.16.N-r18869.tar.xz](https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/Touch/kindle-jailbreak-1.16.N-r18869.tar.xz)
  - Unpack `kindle-5.4-jailbreak.zip` and upload the **files of the extracted folder** to the Kindle's root directory
  - Disconnect the USB and _Update Kindle_ from the settings
  - If it worked you should see the message `**** JAILBREAK ****` appear :)

- Install _KUAL_ (maybe also _KUAL+_)

  - Download [https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/KUAL/KUAL-v2.7.26-g32b2e39-20220213.tar.xz](https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/KUAL/KUAL-v2.7.26-g32b2e39-20220213.tar.xz)
  - Extract and put `KUAL-KDK-2.0.azw2` into Kindles `documents` folder
  - Open the new "book"

- Install _MR Package Installer_

  - Download [https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/KUAL/kual-mrinstaller-1.7.N-r18896.tar.xz](https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/KUAL/kual-mrinstaller-1.7.N-r18896.tar.xz)
  - Extract and put `extensions` (with `MRInstaller`) into Kindle's root folder (or replace / merge the `extensions` folder if it exists)

- Install the _USB Network_ hack
  - Download [https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/Touch/kindle-usbnet-0.22.N-r18897.tar.xz](https://storage.gra.cloud.ovh.net/v1/AUTH_2ac4bfee353948ec8ea7fd1710574097/mr-public/Touch/kindle-usbnet-0.22.N-r18897.tar.xz)
  - Upload the file `Update_usbnet_0.22.N_install_touch_pw.bin` to the Kindle's root `mrpackages` folder (create it if it doesn't exist)
  - Open `KUAL` app and use the `Helper / Install MR Packagages` action
  - There should be `USBNetwork` listed in `KUAL`

#### Setup

- Apply following settings in `KUAL / USBNetwork`:

  ```
  Enable SSH at boot
  Make dropbear quiet
  Toggle USB Network
  ```

- Add the Rasperry Pi's public SSH key to `/mnt/us/etc/authorized_keys` (the file must be created manually if it doesn't exist)

#### Useful links

- [https://wiki.mobileread.com/wiki/Kindle_Serial_Numbers](https://wiki.mobileread.com/wiki/Kindle_Serial_Numbers)
- [Kindle Touch/PW1/PW2 5.0.x - 5.4.4.2 JailBreak. Plus FW 5.x USBNetwork.](https://www.mobileread.com/forums/showthread.php?t=186645)
- [Kindle Touch Hacking](https://wiki.mobileread.com/wiki/Kindle_Touch_Hacking#CURRENT_UNIVERSAL_METHOD)
