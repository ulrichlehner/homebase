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
| `live_meter_entity`    | optional: a live energy counter of the meter in Home Assistant (total in kWh), for example from the meter's wM-Bus radio: `sensor.home_grid_total_energy_consumption` (see below) |

The portal shows a day only one to two days later, so fetching more often brings nothing new;
the statistic is still rebuilt every hour from the files the add-on already has.

## What it does

Every hour, a few minutes past the full hour, the add-on

1. takes over history files you dropped into its `import/` folder (see below),
2. fetches new readings from the portal when `fetch_interval_hours` have passed since the last
   successful fetch (the first run downloads everything the portal still has, about three years),
3. imports one external statistic, `linznetz_energy:<site>_grid_consumption`, shown as
   "<Site> grid consumption" (in German "<Site> Netzbezug"): the best source per hour (the live meter
   readings, then the portal readings, then the imported reconstructed history). It is rebuilt
   completely every time, so values the portal delivers or corrects later end up in Home Assistant
   too. This is the statistic for the Energy dashboard.

The live meter and the portal are compared by the add-on itself (its page, the status entities and a
notification), so there is no separate portal statistic; the one written by versions before 1.0.0
is deleted once.

Pick the statistic in a statistics graph card, or in the Energy dashboard as grid consumption.

## Live meter readings (optional)

Any entity in Home Assistant that has the meter's energy counter live (a total in kWh) can serve as
the live source: for example the meter's wM-Bus radio received with the wmbusmeters add-on and the
`amiplus_linznetz` driver of this repository, or a reader on the meter's customer interface. Enter
that entity as `live_meter_entity`, and the add-on

- reads the new counter readings every hour (only reads, nothing is changed in Home Assistant),
- cleans them: repeated values count from their first occurrence, phases in which the meter sends
  wrong values (a tenth of the counter, near-zero values, spikes) are dropped, and an hour gets a
  value only if good readings at most 60 minutes apart surround both of its ends (or the counter
  did not move in between, as during hours of PV export),
- makes them the **primary source** of the consumption statistic: the hours since the last portal
  day come from the live meter right away, and where it has no value (a gap, a misread phase) the
  portal fills in a day or two later,
- compares the live meter with the portal for every day both have complete, and sends a notification
  when a day differs by more than 2 % (the live value is kept),
- archives the readings in its own folder: the raw readings of the last 90 days and the cleaned
  hourly values for good. Home Assistant's recorder drops raw states after 10 days by default; the
  archive is independent of that.

## Switching the Energy dashboard

The statistic "<Site> grid consumption" ("<Site> Netzbezug") is meant for the Energy dashboard: the
whole history, the live meter readings, gaps filled by the portal. The add-on never changes the
dashboard itself; its page shows what the dashboard uses now and what to do:

1. Make a backup (Settings > System > Backups).
2. Open Settings > Dashboards > Energy. Under **grid consumption** add "<Site> grid consumption"
   and remove the sensor used so far (for example the live counter); keeping both counts the
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
| Live data until | the end of the newest hour with a live value |
| Live deviation last day | the deviation on the last day both have complete, in % |
| Deviating hours last 7 days | live hours of the last 7 days that differ from the portal |
| Portal login problem | on while the portal rejects the login |

Without a broker the add-on works the same, only without these entities.

## The add-on page

**Open Web UI** on the add-on page (or the sidebar entry, if you switch on "Show in sidebar") opens
the add-on's page inside Home Assistant. It shows

- what the add-on does right now (first download, import, writing statistics), refreshing itself
  while something runs, plus the last and next run and the portal login,
- the data it has: portal readings and reconstructed history with their ranges,
- the statistic with its range, total and sources, and the live vs portal check,
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

## Uninstalling

Uninstalling an add-on does not touch what it created in Home Assistant itself: the statistic lives
in Home Assistant's database, and the device with the status entities is kept by the MQTT broker.
So, before uninstalling:

1. If the Energy dashboard uses "<Site> grid consumption", switch it back to another source first.
2. On the add-on page, at the bottom: **Remove everything from Home Assistant**. It deletes the
   statistic, the device with its status entities and the add-on's notifications, and pauses the
   add-on so nothing is created again. (It refuses while the Energy dashboard still uses the
   statistic. Changed your mind? **Reactivate** brings everything back within an hour.)
3. Uninstall the add-on and tick the option to also delete its data: that removes its own files
   (the readings archive). Backups that include the add-on keep them.
