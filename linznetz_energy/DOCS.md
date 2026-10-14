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

1. fetches new readings from the portal when `fetch_interval_hours` have passed since the last
   successful fetch (the first run downloads the complete history, about three years),
2. imports every hour as the external statistic `linznetz:<site>_grid_energy_portal`
   ("<Site> grid energy (portal)"). The statistic is rebuilt completely every time, so values the
   portal corrects later are corrected in Home Assistant too.

Pick the statistic in a statistics graph card, or in the Energy dashboard as grid consumption.

If the portal rejects the login, a notification appears in Home Assistant and the add-on tries
again after `fetch_interval_hours`; the notification disappears after the next successful login.

## Data

The CSV files (readings and the raw portal exports) live in the add-on configuration folder
`/addon_configs/<id>_linznetz_energy/`, reachable with the Samba or SSH add-on, and are part
of every Home Assistant backup that includes the add-on.

To remove the statistic: Developer tools > Statistics, search `linznetz:`, delete.
