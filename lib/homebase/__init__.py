"""homebase: smart meter readings of LINZ NETZ, shared by the command line tools (databot/)
and the Home Assistant add-on (linznetz_energy/).

  portal         HTTP client for the LINZ NETZ portal and its CSV export
  readings       the readings archive (CSV files): update from the portal, unified file
  disaggregate   split reconstructed 6-hour blocks into quarter hours by a typical profile
  fusion         live counter cleaning, one hourly series from live counter, portal and reconstruction
  livearchive    the archive of the live meter counter (raw readings, hourly values)
  hass           Home Assistant websocket client (statistics, history, services)
"""
