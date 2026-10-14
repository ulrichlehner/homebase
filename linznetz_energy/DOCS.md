# LINZ NETZ Energy

> Not affiliated with LINZ NETZ GmbH. The add-on uses the public LINZ NETZ customer portal with your own
> login, the same way you would in the browser.

Fetches the quarter-hour readings of your smart meter from the
[LINZ NETZ service portal](https://www.linznetz.at/portal/de/home/online_services/serviceportal) and
writes them into Home Assistant as a long-term statistic for the Energy dashboard. Next steps
(fusion with the live wM-Bus readings of the meter) are planned in
[docs/energy-fusion-plan.md](https://github.com/ulrichlehner/homebase/blob/main/docs/energy-fusion-plan.md).

## Prerequisites

- A LINZ NETZ portal account with the quarter-hour analysis ("Viertelstundenauswertung") enabled
  for the meter. The portal shows the values of a day one to two days later.

## First start

Install the add-on (Home Assistant now calls add-ons "apps"), fill in the **Configuration** tab
(portal username, portal password and the metering point; everything else has defaults), save and
start it. If you start it before that, it starts anyway and tells you what is missing: on its page
(**Open Web UI**), in a notification and in the log. After saving the configuration, restart the
add-on on the **Info** tab, and the first download begins.

Note: Home Assistant only enables **Save** after a field was changed. The page and the
notifications follow the language of Home Assistant (German and English; the log stays English).

## Configuration

| Option                 | Meaning                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `portal_username`      | portal login                                                               |
| `portal_password`      | portal password                                                            |
| `metering_point`       | the metering point number (Zählpunktnummer, not the meter number), 33 characters starting with `AT`; spaces, dots and lowercase letters are fine |
| `site`                 | name of the place, shown as you write it (default `Home`); the statistic IDs use a simplified form (`Zweitwohnsitz Linz` → `zweitwohnsitz_linz`) |
| `fetch_interval_hours` | hours between two portal fetches (default 12, 6 to 48)                     |
| `rf_entity`            | optional: the energy counter of the meter's radio (wM-Bus) readings in Home Assistant, e.g. `sensor.home_grid_total_energy_consumption` (see below) |

The portal shows a day only one to two days later, so fetching more often brings nothing new;
the statistic is still rebuilt every hour from the files the add-on already has.

## What it does

Every hour, a few minutes past the full hour, the add-on

1. takes over history files you dropped into its `import/` folder (see below),
2. fetches new readings from the portal when `fetch_interval_hours` have passed since the last
   successful fetch (the first run downloads everything the portal still has, about three years),
3. imports one external statistic, `linznetz_energy:<site>_grid_consumption`, shown as
   "<Site> grid consumption" (in German "<Site> Netzbezug"): the best source per hour (the live radio
   readings, then the portal readings, then the imported reconstructed history). It is rebuilt
   completely every time, so values the portal delivers or corrects later end up in Home Assistant
   too. This is the statistic for the Energy dashboard.

Radio and portal are compared by the add-on itself (its page, the status entities and a
notification), so there is no separate portal statistic; the one written by versions before 1.0.0
is deleted once.

Pick the statistic in a statistics graph card, or in the Energy dashboard as grid consumption.

## Live readings from the meter's radio (optional)

With a wM-Bus receiver (for example the wmbusmeters add-on with the `amiplus_linznetz` driver of this
repository) Home Assistant has the meter's energy counter live. Enter that entity as `rf_entity`, and
the add-on

- reads the new counter readings every hour (only reads, nothing is changed in Home Assistant),
- cleans them: repeated values count from their first occurrence, phases in which the meter sends
  wrong values (a tenth of the counter, near-zero values, spikes) are dropped, and an hour gets a
  value only if good readings at most 60 minutes apart surround both of its ends,
- makes them the **primary source** of the consumption statistic: the hours since the last portal
  day come from the radio right away, and where the radio has no value (a gap, a misread phase) the
  portal fills in a day or two later,
- compares the radio with the portal for every day both have complete, and sends a notification
  when a day differs by more than 2 % (the radio value is kept),
- archives the readings in its own folder: the raw readings of the last 90 days and the cleaned
  hourly values for good. Home Assistant's recorder drops raw states after 10 days by default; the
  archive is independent of that.

## Switching the Energy dashboard

The statistic "<Site> grid consumption" ("<Site> Netzbezug") is meant for the Energy dashboard: the
whole history, the radio readings live, gaps filled by the portal. The add-on never changes the
dashboard itself; its page shows what the dashboard uses now and what to do:

1. Make a backup (Settings > System > Backups).
2. Open Settings > Dashboards > Energy. Under **grid consumption** add "<Site> grid consumption"
   and remove the sensor used so far (for example the radio counter); keeping both counts the
   consumption twice.
3. Check a few days in the Energy dashboard against your bill or the portal.

This can be undone at any time: the old statistics of the sensor stay, switch back the same way.

## Status entities

If an MQTT broker is set up in Home Assistant (for example the Mosquitto add-on), the add-on creates
a device **LINZ NETZ Energy <site>** with these entities (no credentials needed, the add-on gets the
broker from Home Assistant):

| Entity | Content |
| --- | --- |
| Last portal fetch | when the portal was fetched successfully the last time |
| Portal data until | the end of the newest portal reading |
| Radio data until | the end of the newest hour with a radio value |
| Radio vs portal (last day) | the deviation on the last day both have complete, in % |
| Deviating hours (7 days) | radio hours of the last 7 days that differ from the portal |
| Portal login problem | on while the portal rejects the login |

Without a broker the add-on works the same, only without these entities.

## The add-on page

**Open Web UI** on the add-on page (or the sidebar entry, if you switch on "Show in sidebar") opens
the add-on's page inside Home Assistant. It shows

- what the add-on does right now (first download, import, writing statistics), refreshing itself
  while something runs, plus the last and next run and the portal login,
- the data it has: portal readings and reconstructed history with their ranges,
- the statistic with its range, total and sources, and the radio vs portal check,
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
