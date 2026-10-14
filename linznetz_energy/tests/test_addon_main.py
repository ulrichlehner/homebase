import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import main as addon
from homebase import disaggregate, readings
from homebase import portal as linznetz

UTC = ZoneInfo('UTC')
ZONE = ZoneInfo('Europe/Vienna')
METER = 'AT0000000000000000000000000000000'
NOW = datetime(2026, 10, 14, 12, 7, tzinfo=UTC)


def options(**kw):
    return addon.Options(**{'portal_username': 'u', 'portal_password': 'secret-pw', 'metering_point': METER, 'site': 'Place', **kw})


class FakeHass:
    def __init__(self):
        self.imports = []
        self.services = []

    def import_statistics(self, metadata, stats):
        self.imports.append((metadata, list(stats)))

    def call_service(self, domain, service, data=None):
        self.services.append((domain, service, data))

    def call(self, type_, **payload):
        if type_ == 'energy/get_prefs':
            return self.energy_prefs
        assert type_ == 'get_config'
        return {'language': self.language, 'time_zone': 'Europe/Vienna'}

    language = 'en'
    energy_prefs = {'energy_sources': [{'type': 'grid', 'flow_from': [
        {'stat_energy_from': 'sensor.place_grid_total_energy_consumption'}], 'flow_to': []},
        {'type': 'solar', 'stat_energy_from': 'sensor.pv'}]}


def write_readings(store: Path, hours: int, start=None, kwh='0.100', name=None):
    start = start or datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    with (store / (name or f'{METER}_readings.csv')).open('w') as fh:
        fh.write('start_local;start_utc;kwh;kw;substitute\n')
        for i in range(hours * 4):
            q = start + timedelta(minutes=15 * i)
            fh.write(f'{q.astimezone(ZONE).isoformat()};{q.strftime("%Y-%m-%dT%H:%M:%SZ")};{kwh};0.400;false\n')


def write_reconstructed(folder: Path, start: datetime, hours: int, name=None):
    with (folder / (name or f'{METER}_reconstructed_15min.csv')).open('w') as fh:
        fh.write(';'.join(disaggregate.OUTPUT_HEADER) + '\n')
        for i in range(hours * 4):
            q = start + timedelta(minutes=15 * i)
            fh.write(f'{q.astimezone(ZONE).isoformat()};{(q + timedelta(minutes=15)).astimezone(ZONE).isoformat()};'
                     f'0.050;chart_ocr;profile;synthetic\n')


def test_load_options(tmp_path):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': f' {METER} ',
                                'site': 'Place_1'}))
    o, problems = addon.load_options(path)
    assert problems == []
    assert o.metering_point == METER and o.site == 'Place_1' and o.site_id == 'place_1' and o.fetch_interval_hours == 12
    assert o.portal_statistic == 'linznetz_energy:place_1_grid_consumption_portal'
    assert o.consumption_statistic == 'linznetz_energy:place_1_grid_consumption'


def test_metering_point_and_site_are_normalized(tmp_path):
    path = tmp_path / 'options.json'
    spaced = 'at 0031 0000.0000-0000 0000 0000 0000 000'    # 33 characters without separators
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': spaced,
                                'site': ' Second Home '}))
    o, problems = addon.load_options(path)
    assert problems == [] and o.metering_point == 'AT0031' + '0' * 27
    assert o.site == 'Second Home' and o.site_id == 'second_home'
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': METER}))
    assert addon.load_options(path)[0].site == 'Home'                   # the default
    assert addon.load_options(path)[0].consumption_statistic == 'linznetz_energy:home_grid_consumption'
    pasted = '\u200b' + METER[:10] + '\u00a0' + METER[10:] + ' \n'   # zero-width, no-break space, line break
    assert addon.normalize_metering_point(pasted) == METER


def test_an_empty_configuration_is_explained(tmp_path):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps({'portal_username': '', 'portal_password': '', 'metering_point': '', 'site': 'home'}))
    o, problems = addon.load_options(path)
    assert o is None and len(problems) == 3
    assert [key for key, _ in problems] == ['problem.username', 'problem.password', 'problem.metering_point']
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': 'AT00310000'}))
    assert addon.load_options(path)[1] == [('problem.metering_point_length', {'count': 10})]
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': 'DE' + METER[2:]}))
    assert addon.load_options(path)[1] == [('problem.metering_point_prefix', {})]
    assert addon.load_options(tmp_path / 'missing.json')[0] is None    # no options file at all


def test_setup_notification_lists_the_problems_and_goes_away(tmp_path, monkeypatch):
    hass = FakeHass()
    hass.close = lambda: None
    monkeypatch.setattr(addon, 'connect', lambda: hass)
    addon.announce_setup(tmp_path, [('problem.password', {})])
    created = hass.services[-1]
    assert created[1] == 'create' and created[2]['notification_id'] == addon.SETUP_NOTIFICATION
    assert created[2]['title'] == 'LINZ NETZ Energy: setup needed'
    assert '- The portal password is missing.' in created[2]['message'] and 'Configuration' in created[2]['message']
    assert addon.web.read_status(tmp_path)['phase'] == 'setup'
    # in the language of Home Assistant
    hass.language = 'de-AT'
    addon.announce_setup(tmp_path, [('problem.password', {})])
    created = hass.services[-1]
    assert created[2]['title'] == 'LINZ NETZ Energy: Einrichtung nötig'
    assert '- Das Portal-Passwort fehlt.' in created[2]['message'] and 'Tab Konfiguration' in created[2]['message']
    addon.announce_setup(tmp_path, [])
    assert hass.services[-1] == ('persistent_notification', 'dismiss', {'notification_id': addon.SETUP_NOTIFICATION})


def test_portal_due():
    assert addon.portal_due({}, NOW, 12)
    assert not addon.portal_due({'portal_ok': (NOW - timedelta(hours=11)).isoformat()}, NOW, 12)
    assert addon.portal_due({'portal_ok': (NOW - timedelta(hours=12)).isoformat()}, NOW, 12)
    # a rejected login waits the full interval too
    assert not addon.portal_due({'portal_ok': (NOW - timedelta(days=3)).isoformat(),
                                 'portal_login_failed': (NOW - timedelta(hours=1)).isoformat()}, NOW, 12)


def test_next_run():
    assert addon.next_run(datetime(2026, 10, 14, 12, 3, tzinfo=UTC)) == datetime(2026, 10, 14, 12, 7, tzinfo=UTC)
    assert addon.next_run(datetime(2026, 10, 14, 12, 7, tzinfo=UTC)) == datetime(2026, 10, 14, 13, 7, tzinfo=UTC)


def test_run_once_imports_the_portal_statistic_in_chunks(tmp_path, monkeypatch):
    write_readings(tmp_path, hours=12)
    monkeypatch.setattr(addon, 'IMPORT_CHUNK', 5)
    fetched = []
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: fetched.append(a))
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert len(fetched) == 1
    # portal and fused statistic, each in chunks of 5 hours
    assert [(m['statistic_id'], len(stats)) for m, stats in hass.imports] == \
        [('linznetz_energy:place_grid_consumption_portal', 5)] * 2 + [('linznetz_energy:place_grid_consumption_portal', 2)] + \
        [('linznetz_energy:place_grid_consumption', 5)] * 2 + [('linznetz_energy:place_grid_consumption', 2)]
    metadata = hass.imports[0][0]
    assert metadata['source'] == 'linznetz_energy' and metadata['name'] == 'Place grid consumption (portal)'
    assert hass.imports[3][0]['name'] == 'Place grid consumption'
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(minutes=1), 'de')
    assert {m['name'] for m, _ in hass.imports[-6:]} == {'Place Netzbezug', 'Place Netzbezug (Portal)'}
    last = hass.imports[-1][1][-1]
    assert last['sum'] == pytest.approx(4.8) and last['start'].minute == 0
    assert ('persistent_notification', 'dismiss', {'notification_id': addon.LOGIN_NOTIFICATION}) in hass.services
    assert 'portal_ok' in addon.read_state(tmp_path)
    # the next hour: no portal fetch, the statistic is imported again
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert len(fetched) == 1 and len(hass.imports) == 18   # three runs, 6 import calls each


def test_rejected_login_notifies_and_keeps_going(tmp_path, monkeypatch, caplog):
    write_readings(tmp_path, hours=2)

    def reject(*a):
        raise linznetz.LoginError('LINZ NETZ rejected the credentials')
    monkeypatch.setattr(addon, 'fetch_portal', reject)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    created = [s for s in hass.services if s[1] == 'create']
    assert created and created[0][2]['notification_id'] == addon.LOGIN_NOTIFICATION
    assert len(hass.imports) == 2   # the existing readings are still imported (portal and fused)
    assert 'portal_login_failed' in addon.read_state(tmp_path)
    assert 'secret-pw' not in caplog.text and 'secret-pw' not in json.dumps(hass.services)


def test_other_portal_errors_retry_next_hour(tmp_path, monkeypatch):
    def broken(*a):
        raise linznetz.LinzNetzError('giving up after 4 attempts')
    monkeypatch.setattr(addon, 'fetch_portal', broken)
    hass = FakeHass()
    write_readings(tmp_path, hours=0)   # not the first download
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert hass.services == [] and hass.imports == []
    assert addon.portal_due(addon.read_state(tmp_path), NOW + timedelta(hours=1), 12)


# -- History import (step 5) --------------------------------------------------------

def test_import_merges_readings_and_existing_rows_win(tmp_path):
    early = datetime(2023, 9, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=2, start=early + timedelta(hours=1), kwh='0.200')   # the add-on's file
    (tmp_path / 'import').mkdir()
    write_readings(tmp_path / 'import', hours=3, start=early, kwh='0.100')            # older archive
    lines = addon.import_history(tmp_path, METER)
    assert lines == [('import.readings', {'file': f'{METER}_readings.csv', 'added': 4, 'total': 12})]
    assert addon.render('de', *lines[0]) == f'{METER}_readings.csv: 4 von 12 Werten ergänzt'
    rows = readings.read_csv_file(tmp_path / f'{METER}_readings.csv')
    assert len(rows) == 12
    values = [rows[k][2] for k in sorted(rows)]
    assert values[:4] == ['0.100'] * 4 and values[4:] == ['0.200'] * 8   # overlap: existing wins
    assert (tmp_path / 'import' / 'done' / f'{METER}_readings.csv').exists()
    assert addon.import_history(tmp_path, METER) == []                   # nothing left: idempotent


def test_import_rejects_other_meters_and_bad_files(tmp_path):
    folder = tmp_path / 'import'
    folder.mkdir()
    other = 'AT0000000000000000000000000000099'
    write_readings(folder, hours=1, name=f'{other}_readings.csv')
    (folder / f'{METER}_readings.csv').write_text('a;b\n1;2\n')
    (folder / f'{METER}_all.csv').write_text('x\n')
    lines = addon.import_history(tmp_path, METER)
    assert len(lines) == 3 and all(key == 'import.failed' for key, _ in lines)
    assert sorted(v['reason_key'] for _, v in lines) == ['import.bad_header', 'import.other_meter', 'import.unknown_file']
    assert addon.render('en', *lines[0]).endswith('(only <metering point>_readings.csv and '
                                                  '<metering point>_reconstructed_15min.csv can be imported)')
    assert sorted(p.name for p in (folder / 'failed').iterdir()) == sorted(
        [f'{other}_readings.csv', f'{METER}_readings.csv', f'{METER}_all.csv'])
    assert not (tmp_path / f'{METER}_readings.csv').exists()


def test_fused_statistic_includes_the_reconstructed_history(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    portal_start = datetime(2023, 9, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=2, start=portal_start)
    (tmp_path / 'import').mkdir()
    write_reconstructed(tmp_path / 'import', portal_start - timedelta(hours=3), hours=3)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    by_id = {}
    for metadata, stats in hass.imports:
        by_id.setdefault(metadata['statistic_id'], []).extend(stats)
    portal, fused = by_id['linznetz_energy:place_grid_consumption_portal'], by_id['linznetz_energy:place_grid_consumption']
    assert len(portal) == 2 and len(fused) == 5
    assert fused[0]['start'] == portal_start - timedelta(hours=3)
    assert fused[-1]['sum'] == pytest.approx(3 * 0.2 + 2 * 0.4)        # 3 reconstructed + 2 portal hours
    created = [s for s in hass.services if s[1] == 'create']
    assert created and created[0][2]['notification_id'] == addon.IMPORT_NOTIFICATION
    assert '12 reconstructed quarter hours taken over' in created[0][2]['message']


def test_first_download_is_announced_and_reported(tmp_path, monkeypatch):
    def download(options, store, tz):
        write_readings(store, hours=3)
    monkeypatch.setattr(addon, 'fetch_portal', download)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    msgs = [s[2] for s in hass.services if s[1] == 'create' and s[2]['notification_id'] == addon.DOWNLOAD_NOTIFICATION]
    assert [m['title'] for m in msgs] == ['LINZ NETZ Energy: first download', 'LINZ NETZ Energy: first download done']
    assert msgs[1]['message'].startswith('12 quarter-hour readings from 01.10.2026 to 01.10.2026 02:45')
    # later fetches are not announced
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=13))
    assert len([s for s in hass.services if s[2].get('notification_id') == addon.DOWNLOAD_NOTIFICATION]) == 2


def test_failed_first_download_is_reported(tmp_path, monkeypatch):
    def broken(*a):
        raise linznetz.LinzNetzError('giving up after 4 attempts')
    monkeypatch.setattr(addon, 'fetch_portal', broken)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    titles = [s[2]['title'] for s in hass.services if s[1] == 'create']
    assert titles == ['LINZ NETZ Energy: first download', 'LINZ NETZ Energy: first download failed']


@pytest.mark.parametrize('name,expected', [
    ('Lakeside', 'lakeside'), ('lakeside', 'lakeside'),            # existing lowercase IDs stay the same
    ('Zweitwohnsitz Linz', 'zweitwohnsitz_linz'), ('Haus am Fluss-Ufer', 'haus_am_fluss_ufer'),
    ('Süßes Häuschen', 'suesses_haeuschen'), ('Café Élise', 'cafe_elise'), ('  Home  ', 'home'),
])
def test_site_id(name, expected):
    assert addon.site_id(name) == expected


def test_site_without_letters_is_a_problem(tmp_path):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': METER, 'site': '***'}))
    assert addon.load_options(path)[1] == [('problem.site', {})]


# -- Step 6: RF readings, comparison and status entities ------------------------------------------

class FakeHassWithHistory(FakeHass):
    def __init__(self, states):
        super().__init__()
        self.states = states
        self.history_calls = []

    def history(self, entity_id, start, end=None):
        self.history_calls.append((entity_id, start))
        return [(t, s) for t, s in self.states if t >= start]


def rf_states(start, hours, watts=400, base=12_000_000):
    return [(start + timedelta(minutes=m), f'{(base + round(watts * m / 60)) / 1000:.3f}') for m in range(0, hours * 60 + 1)]


def test_rf_becomes_the_primary_source_and_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=48, start=start)               # portal: 400 Wh per hour, two full days
    hass = FakeHassWithHistory(rf_states(start, 50))             # RF: the same, two hours further
    o = options(rf_entity='sensor.place_grid_total_energy_consumption')
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', start + timedelta(hours=50, minutes=7))
    assert hass.history_calls[0][0] == 'sensor.place_grid_total_energy_consumption'
    consumption = [stats for m, stats in hass.imports if m['statistic_id'].endswith('_grid_consumption')]
    hours = [s for chunk in consumption for s in chunk]
    assert len(hours) == 50 and hours[-1]['sum'] == pytest.approx(50 * 0.4)   # RF covers the two extra hours
    status = addon.web.read_status(tmp_path)
    assert status['rf_check']['last_day'] == '2026-10-02' and status['rf_check']['last_day_percent'] == 0.0
    assert status['rf_check']['bad_days'] == [] and status['data']['rf']['count'] == 50
    # the next run only asks HA for what is new
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', start + timedelta(hours=51, minutes=7))
    assert hass.history_calls[1][1] == start + timedelta(hours=49)   # last raw reading minus one hour


def test_a_deviating_day_is_notified_once(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=24, start=start)               # portal: 9.6 kWh that day
    hass = FakeHassWithHistory(rf_states(start, 24, watts=500))    # RF: 12 kWh, 25 % more
    o = options(rf_entity='sensor.place_grid_total_energy_consumption')
    now = start + timedelta(hours=26)
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', now)
    notes = [s[2] for s in hass.services if s[2].get('notification_id') == addon.DEVIATION_NOTIFICATION]
    assert len(notes) == 1 and '2026-10-01: 12.00 / 9.60 kWh' in notes[0]['message']
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', now + timedelta(hours=1))
    assert len([s for s in hass.services if s[2].get('notification_id') == addon.DEVIATION_NOTIFICATION]) == 1


def test_rf_entity_must_look_like_an_entity(tmp_path):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': METER,
                                'rf_entity': 'Lakeside grid'}))
    assert addon.load_options(path)[1] == [('problem.rf_entity', {'value': 'Lakeside grid'})]


def test_status_entities_over_mqtt(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: {'host': 'core-mosquitto'})
    monkeypatch.setattr(addon.mqtt_status, 'publish', lambda *a: published.append(a) or True)
    write_readings(tmp_path, hours=2)
    addon.publish_status(options(), tmp_path, 'de', {'portal_ok': '2026-10-14T12:00:00+00:00'}, None, ZONE)
    connection, site, site_id, lang, version, values = published[0]
    assert (site, site_id, lang) == ('Place', 'place', 'de')
    assert values['portal_data_until'] == '2026-10-01T00:00:00+00:00'                 # end of the last quarter hour
    assert values['portal_login_problem'] is False and values['rf_deviation'] is None


def test_mqtt_messages():
    import mqtt_status
    msgs = dict(mqtt_status.messages('Lakeside', 'lakeside', 'de', '0.3.0', {'rf_deviation': 0.4}))
    config = json.loads(msgs['homeassistant/sensor/linznetz_energy_lakeside/rf_deviation/config'])
    assert config['name'] == 'Funk vs. Portal (letzter Tag)' and config['unit_of_measurement'] == '%'
    assert config['default_entity_id'] == 'sensor.lakeside_linznetz_rf_deviation'
    assert config['unique_id'] == 'linznetz_energy_lakeside_rf_deviation'
    assert config['device']['name'] == 'LINZ NETZ Energy Lakeside'
    problem = json.loads(msgs['homeassistant/binary_sensor/linznetz_energy_lakeside/portal_login_problem/config'])
    assert problem['device_class'] == 'problem' and problem['entity_category'] == 'diagnostic'
    state = json.loads(msgs['linznetz_energy/lakeside/state'])
    assert state['rf_deviation'] == 0.4 and state['last_portal_fetch'] is None



# -- Step 7: the Energy dashboard -------------------------------------------------------------------

def test_energy_status_reads_the_grid_sources():
    hass = FakeHass()
    status = addon.energy_status(hass, options())
    assert status == {'grid_from': ['sensor.place_grid_total_energy_consumption'],   # solar is ignored
                      'consumption_statistic': 'linznetz_energy:place_grid_consumption', 'uses_consumption': False}
    hass.energy_prefs = {'energy_sources': [{'type': 'grid', 'flow_from': [
        {'stat_energy_from': 'linznetz_energy:place_grid_consumption'}]}]}
    assert addon.energy_status(hass, options())['uses_consumption'] is True
