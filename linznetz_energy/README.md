# LINZ NETZ energy

LINZ NETZ portal readings as long-term statistics for the Home Assistant Energy dashboard.

- Fetches the quarter-hour readings of your smart meter from the LINZ NETZ service portal.
- Imports them every hour as the statistic `linznetz:<site>_grid_energy_portal`, rebuilt
  completely so later portal corrections show up too.
- Keeps the readings as CSV files in its own config folder, part of every backup.
- Tells you with a notification when the portal rejects the login.

Next: fusion with the live wM-Bus readings of the meter, so the Energy dashboard shows the
portal history and the current hour in one series. See the Documentation tab.
