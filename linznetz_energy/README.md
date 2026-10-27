# LINZ NETZ Energy

The consumption of your LINZ NETZ smart meter for the Home Assistant Energy dashboard: live from a meter
reading in Home Assistant, completed and checked with the LINZ NETZ portal, with the whole history.

- Fetches the quarter-hour readings of your smart meter from the LINZ NETZ service portal.
- Takes a live meter reading in Home Assistant (for example from the meter's wM-Bus radio) as the
  primary source and fills their gaps
  from the portal.
- Imports one statistic, `linznetz_energy:<site>_grid_consumption`, for the Energy dashboard,
  rebuilt completely so later portal corrections show up too.
- Keeps the readings as CSV files in its own config folder, part of every backup.
- Tells you with a notification when the portal rejects the login.

See the Documentation tab.

> Not affiliated with LINZ NETZ GmbH. The app uses the public LINZ NETZ customer portal with your own
> login, the same way you would in the browser.
