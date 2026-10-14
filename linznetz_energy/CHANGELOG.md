# Changelog

## 0.1.1

- The option `portal_hours` is now called `fetch_interval_hours`.
- German translation of the options.

## 0.1.0

- First version: fetches the LINZ NETZ portal every 12 hours into CSV files in the add-on
  config folder and imports `linznetz:<site>_grid_energy_portal` every hour.
- A rejected portal login raises a notification in Home Assistant.
