# LINZ NETZ energy

Fetches the quarter-hour readings of your smart meter from the
[LINZ NETZ service portal](https://www.linznetz.at/portal/de/home/online_services/serviceportal) and
writes them into Home Assistant as a long-term statistic for the Energy dashboard. Next steps
(fusion with the live wM-Bus readings of the meter) are planned in
[docs/energy-fusion-plan.md](https://github.com/ulrichlehner/homebase/blob/main/docs/energy-fusion-plan.md).

## Prerequisites

- A LINZ NETZ portal account with the quarter-hour analysis ("Viertelstundenauswertung") enabled
  for the meter. The portal shows the values of a day one to two days later.

## Configuration

| Option                 | Meaning                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `username`             | portal login                                                               |
| `password`             | portal password                                                            |
| `meter_id`             | the 33-character metering point number (Zählpunktnummer), starts with `AT`  |
| `site`                 | short lowercase name of the place, part of the statistic ID                |
| `fetch_interval_hours` | hours between two portal fetches (default 12, 6 to 48)                     |

The portal shows a day only one to two days later, so fetching more often brings nothing new;
the statistic is still rebuilt every hour from the files the add-on already has.

## What it does

Every hour, a few minutes past the full hour, the add-on

1. takes over history files you dropped into its `import/` folder (see below),
2. fetches new readings from the portal when `fetch_interval_hours` have passed since the last
   successful fetch (the first run downloads everything the portal still has, about three years),
3. imports two external statistics, rebuilt completely every time, so values the portal corrects
   later are corrected in Home Assistant too:

   | Statistic                             | Content                                                      |
   | ------------------------------------- | ------------------------------------------------------------ |
   | `linznetz:<site>_grid_energy_portal`  | the portal readings only                                     |
   | `linznetz:<site>_grid_energy_fused`   | the best source per hour: portal readings, then the imported reconstructed history; the live wM-Bus readings follow |

Pick a statistic in a statistics graph card, or in the Energy dashboard as grid consumption.

## Bringing older history

The portal keeps only about 36 months. If you have older data from the homebase tools, the add-on
can take it over:

1. Open the add-on configuration folder, for example with the Samba add-on: share
   `addon_configs`, folder `<id>_linznetz_energy`. It contains an `import` folder (created on the
   first start).
2. Copy any of these files into `import/` (the names start with your metering point number):
   - `<meterId>_readings.csv`: older portal readings. They are merged row by row; readings the
     add-on already has win, so the file can overlap with the portal.
   - `<meterId>_reconstructed_15min.csv`: the reconstructed history from `./homebase
     disaggregate`. It replaces an earlier one and becomes part of the fused statistic.
3. Wait for the next full hour (or restart the add-on). A notification lists what was imported;
   the files move to `import/done/`, files that could not be imported to `import/failed/` (the log
   says why). Importing the same file again changes nothing.

Copy the files before the first start to have the full history in the very first import.

If the portal rejects the login, a notification appears in Home Assistant and the add-on tries
again after `fetch_interval_hours`; the notification disappears after the next successful login.

## Data

The CSV files (readings, the reconstructed history and the raw portal exports) live in the add-on
configuration folder `/addon_configs/<id>_linznetz_energy/`, reachable with the Samba or SSH
add-on, and are part of every Home Assistant backup that includes the add-on.

To remove the statistic: Developer tools > Statistics, search `linznetz:`, delete.
