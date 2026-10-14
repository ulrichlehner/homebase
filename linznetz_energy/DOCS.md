# LINZ NETZ energy

Fetches the quarter-hour readings of your smart meter from the
[LINZ NETZ service portal](https://www.linznetz.at/portal/de/home/online_services/serviceportal) and
writes them into Home Assistant as a long-term statistic for the Energy dashboard. Next steps
(fusion with the live wM-Bus readings of the meter) are planned in
[docs/energy-fusion-plan.md](https://github.com/ulrichlehner/homebase/blob/main/docs/energy-fusion-plan.md).

## Prerequisites

- A LINZ NETZ portal account with the quarter-hour analysis ("Viertelstundenauswertung") enabled
  for the meter. The portal shows the values of a day one to two days later.

## First start

Install the add-on, fill in the **Configuration** tab (portal username, portal password and the
metering point; everything else has defaults), save and start it. If you start it before that, it
starts anyway and tells you what is missing: on its page (**Open Web UI**), in a notification and
in the log. Saving the configuration makes Home Assistant restart the add-on, and the first download
begins.

## Configuration

| Option                 | Meaning                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `portal_username`      | portal login                                                               |
| `portal_password`      | portal password                                                            |
| `metering_point`       | the metering point number (Zählpunktnummer, not the meter number), 33 characters starting with `AT`; spaces, dots and lowercase letters are fine |
| `site`                 | short name of the place, part of the statistic ID (default `home`)          |
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

   | Statistic                                         | Content                                          |
   | ------------------------------------------------- | ------------------------------------------------ |
   | `linznetz_energy:<site>_grid_consumption`         | "<Site> grid consumption": the best source per hour (portal readings, then the imported reconstructed history; the live wM-Bus readings follow). This is the one for the Energy dashboard |
   | `linznetz_energy:<site>_grid_consumption_portal`  | "<Site> grid consumption (portal)": the portal readings only, to check the first one |

Pick a statistic in a statistics graph card, or in the Energy dashboard as grid consumption.

## The add-on page

**Open Web UI** on the add-on page (or the sidebar entry, if you switch on "Show in sidebar") opens
the add-on's page inside Home Assistant. It shows

- what the add-on does right now (first download, import, writing statistics), refreshing itself
  while something runs, plus the last and next run and the portal login,
- the data it has: portal readings and reconstructed history with their ranges,
- both statistics with their range, total and sources (and deviating hours, once the live
  readings are part of the consumption statistic),
- the result of the last import.

## Bringing older history

The portal keeps only about 36 months. If you have older data from the homebase tools, upload it on
the add-on page under **Import older history** (the names start with your metering point number):

- `<meterId>_readings.csv`: older portal readings. They are merged row by row; readings the add-on
  already has win, so the file can overlap with the portal.
- `<meterId>_reconstructed_15min.csv`: the reconstructed history from `./homebase disaggregate`. It
  replaces an earlier one and becomes part of the consumption statistic.

The add-on imports the files right away, the page and a notification list what was imported.
Importing the same file again changes nothing. Files that cannot be imported (another metering
point, an unexpected header, another file) are listed with the reason.

Alternatively, copy the files into the `import` folder of the add-on configuration folder (for
example with the Samba add-on); they are picked up on the next full hour. Processed files move to
`import/done/`, rejected ones to `import/failed/`.

## Data

The CSV files (readings, the reconstructed history and the raw portal exports) live in the add-on
configuration folder `/addon_configs/<id>_linznetz_energy/`, reachable with the Samba or SSH
add-on, and are part of every Home Assistant backup that includes the add-on.

To remove the statistics: Developer tools > Statistics, search `linznetz_energy:`, delete.
