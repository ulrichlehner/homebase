# LINZ NETZ Energy

LINZ NETZ portal readings as long-term statistics for the Home Assistant Energy dashboard.

- Fetches the quarter-hour readings of your smart meter from the LINZ NETZ service portal.
- Takes the live radio (wM-Bus) readings of the meter as the primary source and fills their gaps
  from the portal.
- Imports one statistic, `linznetz_energy:<site>_grid_consumption`, for the Energy dashboard,
  rebuilt completely so later portal corrections show up too.
- Keeps the readings as CSV files in its own config folder, part of every backup.
- Tells you with a notification when the portal rejects the login.

See the Documentation tab.

> Not affiliated with LINZ NETZ GmbH. The add-on uses the public LINZ NETZ customer portal with your own
> login, the same way you would in the browser.
