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

### The portal password as a secret

The form hides the password, but the YAML view of the configuration shows it. Keep it in Home
Assistant's `secrets.yaml` instead, the way Home Assistant handles secrets everywhere:

1. Add a line to `secrets.yaml` in the Home Assistant configuration folder, for example with the
   File editor add-on (Settings > Apps > Install app > File editor); keep the quotes:

   ```yaml
   linznetz_password: "your-portal-password"
   ```

2. In the add-on's **Configuration** tab, open the menu (⋮) > **Edit in YAML** and write

   ```yaml
   portal_password: "!secret linznetz_password"
   ```

   then save and restart the add-on.

If saving reports "Unknown secret 'linznetz_password'", Home Assistant has not seen the line yet:
make sure `secrets.yaml` is saved (in File editor the disk icon at the top right is red while
something is unsaved), wait a minute (the Supervisor reads `secrets.yaml` at most once a minute),
reload the page and save again. Double or single quotes around `!secret …` make no difference.

The add-on configuration then only holds the reference; Home Assistant puts the password in when
the add-on starts. The same works for `portal_username`. Choosing the live meter on the add-on page
keeps the reference as it is. Backups contain `secrets.yaml`, so keep them encrypted (the default
for Home Assistant backups).

While the password is stored as plain text, the add-on page shows these steps and Home
Assistant gets one notification with them; the add-on works either way.

There is no fetch interval to set: the add-on knows when the portal can have something new
(see below).

## What it does

Every hour, a few minutes past the full hour, the add-on

1. takes over history files you dropped into its `import/` folder (see below),
2. fetches new readings from the portal when it can have something new: LINZ NETZ publishes a
   day one to two days later, at a time of its own. So while yesterday is not complete on the
   portal yet, the add-on looks every hour between 06:00 and 22:00; once it has arrived, it waits
   for the next day. That is one or a few logins a day. The add-on page shows the next fetch and
   when new days usually arrive. The first run downloads everything the portal still has (about
   three years) right away, **Fetch now** on the add-on page fetches at any time, and after a
   rejected login it waits 6 hours,
3. imports one external statistic, `linznetz_energy:<site>_grid_consumption`, shown as
   "<Site> grid consumption" (in German "<Site> Netzbezug"): the best source per hour (the live meter
   readings, then the portal readings, then the imported reconstructed history). It is rebuilt
   completely every time, so values the portal delivers or corrects later end up in Home Assistant
   too. This is the statistic for the Energy dashboard.

The live meter and the portal are compared by the add-on itself (its page, the status entities and a
notification), so there is no separate portal statistic; the one written by versions before 1.0.0
is deleted once.

Pick the statistic in a statistics graph card, or in the Energy dashboard under **Energy imported
from grid**.

## How the statistic is built

The statistic has one value per hour. For every hour the add-on takes the best source it has:

| Order | Source | When it is used |
| --- | --- | --- |
| 1 | Live meter | the hour has good live readings; available right after the hour |
| 2 | Portal, measured | the portal has all four quarter hours of the hour (one to two days later) |
| 3 | Portal, substitute values | all four quarter hours, but some are substitute values (see below) |
| 4 | Reconstructed history | your imported `_reconstructed_15min.csv`, for months the portal no longer has |
| 5 | Portal, incomplete | some quarter hours are missing; the ones that are there |

**Substitute values** (Ersatzwerte): when LINZ NETZ receives no reading from the meter for a quarter
hour (for example after a transmission fault or a meter change), it calculates one so the bill has
no hole, and marks it in the portal's "Ersatzwert" column. It may replace it with the real reading
later. Such hours rank below measured ones, and they are not used to check the live meter.

A few rules on top:

- **Live first, portal as check.** When the portal later has an hour the live meter already
  delivered, the live value stays. If the two differ by more than 2 % (at least 20 Wh), the hour
  counts as a deviation on the add-on page; whole days are compared too.
- **Reception gaps.** When the meter sends no usable counter for a while, the add-on splits the
  consumption of that gap between the hours by the meter's power readings. Without power readings
  the split is an estimate, and the measured portal value replaces it once the portal has it.
- **Nothing is double.** Each hour has exactly one value from one source, so switching sources
  never counts anything twice.
- **Gaps stay gaps.** An hour no source has gets no value; the running total simply continues.
  The add-on page lists such gaps under **Statistics**, and older readings fill them (see
  "Bringing older history").
- **Rebuilt every hour.** The whole statistic is written again each time, so a value the portal
  delivers or corrects later replaces the earlier one in Home Assistant.

An example day, seen on the next morning: the hours until midnight come from the live meter, one
hour around a reception gap is split by the power readings, and an hour in which the receiver was
off is still empty. A day later the portal has the whole day: the empty hour gets the portal value,
and the add-on checks the live hours against the portal.

## Costs

The add-on also writes the costs of the consumption, as on the bills of grid operator and supplier
together: the statistic `linznetz_energy:<site>_grid_cost`, shown as "<Site> electricity cost" (in
German "<Site> Stromkosten"), a running total in EUR. In the Energy dashboard, pick it at the grid
connection under **Cost tracking**, **Use an entity tracking the total costs**. (For a statistic
like "<Site> grid consumption", Home Assistant offers only this option; a fixed price or a price
sensor cannot be chosen there.)

Every hour costs:

- the kWh of the hour times every per-kWh price valid then: the supplier's energy price, the grid
  charges (Netznutzung, Netzverlust) and the levies (Elektrizitätsabgabe, Erneuerbaren-Förderbeitrag),
- its share of the fixed fees: the supplier's basic fee, the grid's Leistungspreis and Messentgelt,
  the Erneuerbaren-Pauschalen, spread evenly over the hours of their month or year,
- 20 % VAT on all of it; a subsidy such as the Stromkostenzuschuss is deducted after VAT, as on the bill.

**Grid operator.** It follows from the metering point. The add-on knows the charges and levies of
LINZ NETZ since 2023 and is updated when they change (usually in January). For another grid
operator only the supplier is counted until its charges are added.

**Supplier.** You can switch suppliers, so the supplier is set on the add-on page under **Costs**,
**Set the supplier**, from a date:

| Supplier | Prices |
| --- | --- |
| go green energy (strom flex) | the energy price changes every month; the add-on reads it from the same data go green's [flex price page](https://www.gogreenenergy.at/flexpreisentwicklung.html) shows (the last 13 months), as soon as a new month is published, and keeps it. Basic fee, online bonus and the "plus" fee as in the price sheet (editable). |
| Fixed price | any supplier with a price guarantee: energy price and basic fee as entered |

A new supplier from a date ends the previous one; the costs before stay as they were. More
suppliers can be added to the add-on as modules.

**Earlier months.** go green shows the last 13 months; the add-on keeps every month it has
fetched, so from then on nothing is missing. Older months have no energy price; the page lists
them under **Months without energy price** (they count grid charges, levies and fees only). A
price list fills them: upload
`<metering point>_prices.csv` under **Import older history**, for example with the monthly prices
from your bills:

```text
valid_from;valid_to;kind;component;unit;value;vat
2025-01-01;2025-02-01;energy;Energiepreis;ct/kWh;12.50;0.2
2024-07-01;2025-01-01;subsidy;Stromkostenzuschuss;ct/kWh;-2.00;0
```

`kind` is `energy`, `grid`, `levy` or `subsidy`; `unit` is `ct/kWh`, `EUR/month` or `EUR/year`;
values are net, `vat` as a fraction; `valid_to` is the first day no longer covered (empty: open).
For go green's "future" products the energy price includes the future surcharge (the bill lists
it separately). The prices are kept in `prices.csv` in the add-on folder; importing the same list
again changes nothing.

## Live meter readings (optional)

Any entity in Home Assistant that has the meter's energy counter live (a total in kWh) can serve as
the live source: for example the meter's wM-Bus radio received with the wmbusmeters add-on and the
`amiplus_linznetz` driver of this repository, or a reader on the meter's customer interface.

Pick it on the add-on page (**Open Web UI**) under **Live meter**: the list shows the sensors that
count energy upwards (Wh, kWh or MWh, without daily or monthly utility meters) with their current
value, so the right counter is easy to spot. **Save** applies it right away, without a restart.
The choice is kept in the add-on's own folder (and so in its backups); the page is the one place
to pick it.

Choosing another counter later starts a new live archive, because its readings do not continue
the old ones; the hours before stay covered by the portal. With a live meter, the add-on

- reads the new counter readings every hour (only reads, nothing is changed in Home Assistant),
- cleans them: repeated values count from their first occurrence, phases in which the meter sends
  wrong values (a tenth of the counter, near-zero values, spikes) are dropped, and an hour gets a
  value only if good readings at most 60 minutes apart surround both of its ends (or the counter
  did not move in between, as during hours of PV export),
- makes them the **primary source** of the consumption statistic: the hours since the last portal
  day come from the live meter right away, and where it has no value (a gap, a misread phase) the
  portal fills in a day or two later,
- splits the counter across reception gaps by the meter's **power**: the meter sends no usable
  counter for about half an hour now and then, while its power readings keep coming. The add-on
  finds the power sensor of the same device by itself (for the wM-Bus meter
  `…_current_power_consumption`) and keeps minute means of it for 14 days. On real data that
  splits the hours around a gap to within 1 Wh of the portal (an even split was off by up to
  100 Wh). Without a power sensor such hours are split evenly and count as **estimates**: the
  portal value replaces them as soon as the portal has them, and until then the page shows them
  as "estimated hours", an info, not a deviation,
- compares the live meter with the portal for every day both have complete, and sends a notification
  when a day differs by more than 2 % (the live value is kept),
- archives the readings in its own folder: the raw readings of the last 90 days and the cleaned
  hourly values for good. Home Assistant's recorder drops raw states after 10 days by default; the
  archive is independent of that.

## Switching the Energy dashboard

The statistic "<Site> grid consumption" ("<Site> Netzbezug") is meant for the Energy dashboard: the
whole history, the live meter readings, gaps filled by the portal. The add-on never changes the
dashboard itself; its page shows what the dashboard uses now and what to do:

1. **Settings > Backup**: create a backup.
2. **Settings > Energy**, section **Electricity grid**: open **Edit grid connection** (pencil) at
   the grid connection, or **Add grid connection** if there is none yet.
3. Under **Energy imported from grid**, choose "<Site> grid consumption" instead of the sensor used
   so far (for example the live counter) and save. Do not keep both: that counts the consumption
   twice.
4. Check a few days in the Energy dashboard against your bill or the portal.

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
- the statistic with its range, total and sources, its gaps (hours no source covers, for example
  months the portal no longer has; older readings fill them, see below), and the live vs portal check,
- the live meter choice (see above),
- the costs: grid operator, supplier, the price now, totals per year, months without energy price, and the
  form to set the supplier,
- the Energy dashboard card, the history import and the result of the last import,
- at the bottom, removing everything from Home Assistant (see "Uninstalling").

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

## Security

The add-on runs with Ingress (Home Assistant handles the login of its page, and the page only
answers Home Assistant) and its own AppArmor profile (`apparmor.txt`): it may use the network,
read Python and the system files it needs, and write only to its own folders; it starts no other
program. The profile is in **complain** mode for now: nothing is blocked, access outside the
rules is only logged by the host as `apparmor="ALLOWED"` with `profile="linznetz_energy"`. Once
these entries stay away, a later version switches to enforcing the rules.

Home Assistant's security rating of the add-on counts the profile once the add-on is installed.

## Uninstalling

Uninstalling an add-on does not touch what it created in Home Assistant itself: the statistic lives
in Home Assistant's database, and the device with the status entities is kept by the MQTT broker.
So, before uninstalling:

1. If the Energy dashboard uses "<Site> grid consumption", switch it back to another source first.
2. On the add-on page, at the bottom: **Remove everything from Home Assistant**. It deletes the
   statistics (consumption and costs), the device with its status entities and the add-on's notifications, and pauses the
   add-on so nothing is created again. (It refuses while the Energy dashboard still uses the
   statistic. Changed your mind? **Reactivate** brings everything back within an hour.)
3. Uninstall the add-on and tick the option to also delete its data: that removes its own files
   (the readings archive). Backups that include the add-on keep them.
