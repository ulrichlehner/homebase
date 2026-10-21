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
NOW = datetime(2026, 10, 14, 12, 15, tzinfo=UTC)   # an hourly run (RUN_MINUTE)


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

    existing_statistics = ()
    cleared = ()

    def statistics_metadata(self, statistic_ids):
        return [{'statistic_id': i} for i in statistic_ids if i in self.existing_statistics]

    def clear_statistics(self, statistic_ids):
        self.cleared = tuple(self.cleared) + tuple(statistic_ids)

    def call(self, type_, **payload):
        if type_ == 'energy/get_prefs':
            if isinstance(self.energy_prefs, Exception):
                raise self.energy_prefs
            return self.energy_prefs
        if type_ == 'get_states':
            return self.states_list
        if type_ == 'config/entity_registry/get':
            return self.registry.get(payload['entity_id'], {})
        if type_ == 'config/entity_registry/list':
            return [{'entity_id': e, **v} for e, v in self.registry.items()]
        assert type_ == 'get_config'
        return {'language': self.language, 'time_zone': 'Europe/Vienna'}

    language = 'en'
    registry: dict = {}   # entity ID -> {'device_id': ...}; empty: no device, so no power sensor
    states_list = [
        {'entity_id': 'sensor.place_grid_total_energy_consumption', 'state': '12345.678',
         'attributes': {'unit_of_measurement': 'kWh', 'state_class': 'total_increasing', 'device_class': 'energy',
                        'friendly_name': 'Place grid Total energy consumption'}},
        {'entity_id': 'sensor.place_grid_power', 'state': '0.4',
         'attributes': {'unit_of_measurement': 'kW', 'state_class': 'measurement'}},
        {'entity_id': 'sensor.daily_energy', 'state': '3.2',
         'attributes': {'unit_of_measurement': 'kWh', 'state_class': 'total_increasing', 'meter_period': 'daily'}},
        {'entity_id': 'sensor.plug_energy', 'state': '812',
         'attributes': {'unit_of_measurement': 'Wh', 'state_class': 'total'}},
        {'entity_id': 'input_number.energy', 'state': '1', 'attributes': {'unit_of_measurement': 'kWh',
                                                                          'state_class': 'total'}},
    ]
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
    assert o.metering_point == METER and o.site == 'Place_1' and o.site_id == 'place_1'
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


def test_portal_due_looks_for_yesterday_during_the_day():
    day = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=ZONE)
    complete = day(13, 23, 45)                      # yesterday's last quarter hour, seen on the 14th
    missing = day(13, 0, 45)
    assert addon.portal_due({}, day(14, 3), ZONE, None)                    # first download: right away
    assert addon.portal_due({}, day(14, 9, 15), ZONE, missing)              # yesterday missing: the hourly run
    assert not addon.portal_due({}, day(14, 9, 30), ZONE, missing)          # ... not between (no usual time yet)
    assert not addon.portal_due({}, day(14, 23, 15), ZONE, missing)         # ... and not at night
    assert not addon.portal_due({}, day(14, 9, 15), ZONE, complete)         # yesterday complete: wait
    assert addon.portal_due({}, day(15, 6, 15), ZONE, complete)             # the next morning it is missing again
    tried = {'portal_tried': day(14, 9, 15).isoformat()}
    assert not addon.portal_due(tried, day(14, 9, 20), ZONE, missing)      # a woken run does not fetch again
    assert addon.portal_due(tried, day(14, 10, 15), ZONE, missing)
    failed = {'portal_login_failed': day(14, 9).isoformat()}
    assert not addon.portal_due(failed, day(14, 14, 15), ZONE, missing)     # rejected login: 6 hours
    assert addon.portal_due(failed, day(14, 15, 15), ZONE, missing)


def test_around_the_usual_arrival_the_portal_is_checked_every_quarter_hour():
    day = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=ZONE)
    missing = day(13, 0, 45)
    state = {'portal_arrivals': ['2026-10-10T10:07+02:00', '2026-10-11T10:07+02:00', '2026-10-12T09:52+02:00']}
    assert addon.usual_arrival(state['portal_arrivals']) == '10:00'          # rounded to the quarter hour
    assert not addon.portal_due(state, day(14, 8, 45), ZONE, missing)       # before the window: hourly only
    assert [addon.portal_due(state, day(14, 9, m), ZONE, missing) for m in (0, 15, 30, 45)] == [True] * 4
    assert addon.portal_due(state, day(14, 12, 0), ZONE, missing)           # two hours after
    assert not addon.portal_due(state, day(14, 12, 30), ZONE, missing)      # then hourly again
    assert addon.portal_due(state, day(14, 13, 15), ZONE, missing)
    # the loop wakes for these checks between the hourly runs
    assert addon.next_wake(state, day(14, 9, 2), ZONE, missing) == (day(14, 9, 15), True)
    assert addon.next_wake(state, day(14, 9, 16), ZONE, missing) == (day(14, 9, 30), False)
    assert addon.next_wake({}, day(14, 9, 16), ZONE, missing) == (day(14, 10, 15), True)
    complete = day(13, 23, 45)
    assert addon.next_wake(state, day(14, 9, 16), ZONE, complete) == (day(14, 10, 15), True)   # nothing missing


def test_portal_next_and_arrivals():
    day = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=ZONE)
    complete = day(13, 23, 45)
    assert addon.portal_next({}, day(14, 9, 30), ZONE, complete) == day(15, 6, 15)
    assert addon.portal_next({}, day(14, 9, 30), ZONE, day(13, 0)) == day(14, 10, 15)
    state = {}
    addon.note_arrival(state, day(12, 23, 45), complete, day(14, 11, 15), ZONE)   # yesterday arrived
    addon.note_arrival(state, complete, complete, day(14, 12, 15), ZONE)           # nothing new
    assert state['portal_arrivals'] == ['2026-10-14T11:15+02:00']
    assert addon.usual_arrival(['2026-10-12T10:07+02:00', '2026-10-13T11:07+02:00', '2026-10-14T13:07+02:00']) == '11:00'
    assert addon.usual_arrival([]) is None


def test_next_run_and_quarter():
    assert addon.next_run(datetime(2026, 10, 14, 12, 3, tzinfo=UTC)) == datetime(2026, 10, 14, 12, 15, tzinfo=UTC)
    assert addon.next_run(datetime(2026, 10, 14, 12, 15, tzinfo=UTC)) == datetime(2026, 10, 14, 13, 15, tzinfo=UTC)
    assert addon.next_quarter(datetime(2026, 10, 14, 12, 3, tzinfo=UTC)) == datetime(2026, 10, 14, 12, 15, tzinfo=UTC)
    assert addon.next_quarter(datetime(2026, 10, 14, 12, 45, tzinfo=UTC)) == datetime(2026, 10, 14, 13, 0, tzinfo=UTC)


def test_a_quarter_hour_check_does_the_full_run_only_with_new_readings(tmp_path, monkeypatch):
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=24, start=start)
    calls = []
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: calls.append(a))       # the portal has nothing new
    hass = FakeHass()
    check = datetime(2026, 10, 3, 9, 30, tzinfo=ZONE).astimezone(UTC)
    addon.write_state(tmp_path, {'portal_arrivals': ['2026-10-02T09:30+02:00']})
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', check, portal_only=True)
    assert len(calls) == 1 and hass.imports == []                               # looked, nothing new: no statistics
    more = lambda *a: write_readings(tmp_path, hours=48, start=start)            # noqa: E731  yesterday arrives
    monkeypatch.setattr(addon, 'fetch_portal', more)
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', check + timedelta(minutes=15), portal_only=True)
    assert hass.imports                                                          # new readings: the full run follows


def test_run_once_imports_the_consumption_statistic_in_chunks(tmp_path, monkeypatch):
    write_readings(tmp_path, hours=12)
    monkeypatch.setattr(addon, 'IMPORT_CHUNK', 5)
    fetched = []
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: fetched.append(a))
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert len(fetched) == 1
    # one statistic, in chunks of 5 hours
    assert [(m['statistic_id'], len(stats)) for m, stats in hass.imports] == \
        [('linznetz_energy:place_grid_consumption', 5)] * 2 + [('linznetz_energy:place_grid_consumption', 2)]
    metadata = hass.imports[0][0]
    assert metadata['source'] == 'linznetz_energy' and metadata['name'] == 'Place grid consumption'
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(minutes=1), 'de')
    assert {m['name'] for m, _ in hass.imports[-3:]} == {'Place Netzbezug'}
    last = hass.imports[-1][1][-1]
    assert last['sum'] == pytest.approx(4.8) and last['start'].minute == 0
    assert ('persistent_notification', 'dismiss', {'notification_id': addon.LOGIN_NOTIFICATION}) in hass.services
    assert 'portal_ok' in addon.read_state(tmp_path)
    assert len(fetched) == 1                               # a minute later: not again
    # the next hour: yesterday is still missing (the test readings end on 1.10.), so it looks again
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert len(fetched) == 2 and len(hass.imports) == 9    # three runs, 3 import calls each


def test_rejected_login_notifies_the_second_time_and_keeps_going(tmp_path, monkeypatch, caplog):
    write_readings(tmp_path, hours=2)
    results = []

    def portal(*a):
        if results.pop(0) == 'reject':
            raise linznetz.LoginError('LINZ NETZ rejected the credentials')
    monkeypatch.setattr(addon, 'fetch_portal', portal)
    hass = FakeHass()
    # once: maybe another session (the browser, a second tool), no notification yet
    results[:] = ['reject']
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert not [s for s in hass.services if s[1] == 'create'] and len(hass.imports) == 1
    assert 'portal_login_failed' not in addon.read_state(tmp_path)
    # twice in a row: the credentials are wrong
    results[:] = ['reject']
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    created = [s for s in hass.services if s[1] == 'create']
    assert created and created[0][2]['notification_id'] == addon.LOGIN_NOTIFICATION
    assert 'portal_login_failed' in addon.read_state(tmp_path)
    assert 'secret-pw' not in caplog.text and 'secret-pw' not in json.dumps(hass.services)
    # a good login later resets both
    results[:] = ['ok']
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=7))   # 6 hours later, 21:15 local
    state = addon.read_state(tmp_path)
    assert 'portal_login_failed' not in state and 'portal_login_rejected' not in state

def test_other_portal_errors_retry_next_hour(tmp_path, monkeypatch):
    def broken(*a):
        raise linznetz.LinzNetzError('giving up after 4 attempts')
    monkeypatch.setattr(addon, 'fetch_portal', broken)
    hass = FakeHass()
    write_readings(tmp_path, hours=0)   # not the first download
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert hass.services == [] and hass.imports == []
    assert addon.portal_due(addon.read_state(tmp_path), NOW + timedelta(hours=1), ZONE, NOW - timedelta(days=2))


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
    assert list(by_id) == ['linznetz_energy:place_grid_consumption']
    fused = by_id['linznetz_energy:place_grid_consumption']
    assert len(fused) == 5
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


# -- Step 6: live meter readings, comparison and status entities ------------------------------------------

class FakeHassWithHistory(FakeHass):
    def __init__(self, states):
        super().__init__()
        self.states = states
        self.history_calls = []

    def history(self, entity_id, start, end=None):
        self.history_calls.append((entity_id, start))
        return [(t, s) for t, s in self.states if t >= start]


def live_states(start, hours, watts=400, base=12_000_000):
    return [(start + timedelta(minutes=m), f'{(base + round(watts * m / 60)) / 1000:.3f}') for m in range(0, hours * 60 + 1)]


def test_live_counter_becomes_the_primary_source_and_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=48, start=start)               # portal: 400 Wh per hour, two full days
    hass = FakeHassWithHistory(live_states(start, 50))             # live counter: the same, two hours further
    o = options(live_meter_entity='sensor.place_grid_total_energy_consumption')
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', start + timedelta(hours=50, minutes=7))
    assert hass.history_calls[0][0] == 'sensor.place_grid_total_energy_consumption'
    consumption = [stats for m, stats in hass.imports if m['statistic_id'].endswith('_grid_consumption')]
    hours = [s for chunk in consumption for s in chunk]
    assert len(hours) == 50 and hours[-1]['sum'] == pytest.approx(50 * 0.4)   # the live counter covers the two extra hours
    status = addon.web.read_status(tmp_path)
    assert status['live_check']['last_day'] == '2026-10-02' and status['live_check']['last_day_percent'] == 0.0
    assert status['live_check']['bad_days'] == [] and status['data']['live']['count'] == 50
    # the next run only asks HA for what is new
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', start + timedelta(hours=51, minutes=7))
    assert hass.history_calls[1][1] == start + timedelta(hours=49)   # last raw reading minus one hour


def test_a_deviating_day_is_notified_once(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=24, start=start)               # portal: 9.6 kWh that day
    hass = FakeHassWithHistory(live_states(start, 24, watts=500))    # live counter: 12 kWh, 25 % more
    o = options(live_meter_entity='sensor.place_grid_total_energy_consumption')
    now = start + timedelta(hours=26)
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', now)
    notes = [s[2] for s in hass.services if s[2].get('notification_id') == addon.DEVIATION_NOTIFICATION]
    assert len(notes) == 1 and '2026-10-01: 12.00 / 9.60 kWh' in notes[0]['message']
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', now + timedelta(hours=1))
    assert len([s for s in hass.services if s[2].get('notification_id') == addon.DEVIATION_NOTIFICATION]) == 1



def test_status_entities_over_mqtt(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: {'host': 'core-mosquitto'})
    monkeypatch.setattr(addon.mqtt_status, 'publish', lambda *a: published.append(a) or True)
    write_readings(tmp_path, hours=2)
    addon.publish_status(options(), addon.Inputs.read(tmp_path, METER), 'de',
                         {'portal_ok': '2026-10-14T12:00:00+00:00'}, None)
    connection, site, site_id, lang, version, values = published[0]
    assert (site, site_id, lang) == ('Place', 'place', 'de')
    assert values['portal_data_until'] == '2026-10-01T00:00:00+00:00'                 # end of the last quarter hour
    assert values['portal_login_problem'] is False and values['live_deviation'] is None


def test_mqtt_messages():
    import mqtt_status
    msgs = dict(mqtt_status.messages('Lakeside', 'lakeside', 'de', '0.3.0', {'live_deviation': 0.4}))
    config = json.loads(msgs['homeassistant/sensor/linznetz_energy_lakeside/live_deviation/config'])
    assert config['name'] == 'Live-Abweichung letzter Tag' and config['unit_of_measurement'] == '%'
    assert config['default_entity_id'] == 'sensor.lakeside_linznetz_live_deviation'
    assert config['unique_id'] == 'linznetz_energy_lakeside_live_deviation'
    assert config['device']['name'] == 'LINZ NETZ Energy Lakeside'
    problem = json.loads(msgs['homeassistant/binary_sensor/linznetz_energy_lakeside/portal_login_problem/config'])
    assert problem['device_class'] == 'problem' and problem['entity_category'] == 'diagnostic'
    state = json.loads(msgs['linznetz_energy/lakeside/state'])
    assert state['live_deviation'] == 0.4 and state['last_portal_fetch'] is None
    price = json.loads(msgs['homeassistant/sensor/linznetz_energy_lakeside/price_total/config'])
    assert price['name'] == 'Strompreis gesamt' and price['unit_of_measurement'] == 'ct/kWh'
    assert price['state_class'] == 'measurement' and price['suggested_display_precision'] == 2
    assert 'entity_category' not in price and state['price_total'] is None



# -- Step 7: the Energy dashboard -------------------------------------------------------------------

def test_energy_status_reads_the_grid_sources():
    hass = FakeHass()
    status = addon.energy_status(hass, options())
    assert status == {'grid_from': ['sensor.place_grid_total_energy_consumption'],   # solar is ignored
                      'consumption_statistic': 'linznetz_energy:place_grid_consumption', 'uses_consumption': False,
                      'uses_cost': False}
    hass.energy_prefs = {'energy_sources': [{'type': 'grid', 'flow_from': [
        {'stat_energy_from': 'linznetz_energy:place_grid_consumption', 'stat_cost': 'linznetz_energy:place_grid_cost'}]}]}
    status = addon.energy_status(hass, options())
    assert status['uses_consumption'] is True and status['uses_cost'] is True



def test_the_portal_statistic_of_earlier_versions_is_deleted_once(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    write_readings(tmp_path, hours=2)
    hass = FakeHass()
    hass.existing_statistics = ('linznetz_energy:place_grid_consumption_portal',)
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert hass.cleared == ('linznetz_energy:place_grid_consumption_portal',)
    assert addon.read_state(tmp_path)['portal_statistic_removed'] is True
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert hass.cleared == ('linznetz_energy:place_grid_consumption_portal',)       # only once
    assert all(m['statistic_id'] == 'linznetz_energy:place_grid_consumption' for m, _ in hass.imports)


# -- Remove everything from Home Assistant ------------------------------------------------------------

def test_removal_is_refused_while_the_energy_dashboard_uses_the_statistic(tmp_path, monkeypatch):
    hass = FakeHass()
    hass.energy_prefs = {'energy_sources': [{'type': 'grid', 'flow_from': [
        {'stat_energy_from': 'linznetz_energy:place_grid_consumption'}]}]}
    hass.existing_statistics = ('linznetz_energy:place_grid_consumption',)
    addon.web.write_status(tmp_path, removal_request='remove')
    addon.handle_removal_request(hass, options(), tmp_path)
    assert hass.cleared == () and addon.web.read_status(tmp_path)['removal'] == {'state': 'refused'}
    assert not addon.read_state(tmp_path).get('removed')


def test_removal_is_refused_when_the_energy_dashboard_cannot_be_read(tmp_path):
    hass = FakeHass()
    hass.energy_prefs = addon.HomeAssistantError('energy/get_prefs: unknown_error: boom')
    hass.existing_statistics = ('linznetz_energy:place_grid_consumption',)
    addon.web.write_status(tmp_path, removal_request='remove')
    addon.handle_removal_request(hass, options(), tmp_path)
    assert hass.cleared == () and addon.web.read_status(tmp_path)['removal']['state'] == 'failed'
    # never set up: nothing uses the statistic
    hass.energy_prefs = addon.HomeAssistantError('energy/get_prefs: not_found: No prefs')
    assert addon.energy_status(hass, options())['uses_consumption'] is False


def test_a_failed_removal_is_reported_and_nothing_is_paused(tmp_path, monkeypatch):
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: {'host': 'core-mosquitto'})
    monkeypatch.setattr(addon.mqtt_status, 'remove', lambda conn, site_id: False)
    hass = FakeHass()
    addon.web.write_status(tmp_path, removal_request='remove')
    addon.handle_removal_request(hass, options(), tmp_path)
    status = addon.web.read_status(tmp_path)
    assert status['removal']['state'] == 'failed' and status['phase'] == 'idle' and not status.get('removal_request')
    assert not addon.read_state(tmp_path).get('removed')


def test_remove_everything_then_reactivate(tmp_path, monkeypatch):
    removed_devices = []
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: {'host': 'core-mosquitto'})
    monkeypatch.setattr(addon.mqtt_status, 'remove', lambda conn, site_id: removed_devices.append(site_id) or True)
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    hass = FakeHass()
    hass.existing_statistics = ('linznetz_energy:place_grid_consumption', 'linznetz_energy:place_grid_consumption_portal')
    addon.web.write_status(tmp_path, removal_request='remove')
    addon.handle_removal_request(hass, options(), tmp_path)
    assert set(hass.cleared) == set(hass.existing_statistics) and removed_devices == ['place']
    dismissed = {s[2]['notification_id'] for s in hass.services if s[1] == 'dismiss'}
    assert dismissed == set(addon.ALL_NOTIFICATIONS)
    status = addon.web.read_status(tmp_path)
    assert status['removal'] == {'state': 'done'} and status['phase'] == 'removed' and not status.get('removal_request')
    assert addon.read_state(tmp_path)['removed'] is True
    # reactivate
    addon.web.write_status(tmp_path, removal_request='resume')
    addon.handle_removal_request(hass, options(), tmp_path)
    assert 'removed' not in addon.read_state(tmp_path) and addon.web.read_status(tmp_path)['removal'] == {}


def test_mqtt_remove_sends_empty_retained_messages(monkeypatch):
    import sys, types
    import mqtt_status
    sent = []

    class Result:
        def wait_for_publish(self, timeout=None):
            pass
        def is_published(self):
            return True

    class Client:
        def __init__(self, *a, **kw): pass
        def username_pw_set(self, *a): pass
        def connect(self, *a, **kw): pass
        def loop_start(self): pass
        def loop_stop(self): pass
        def disconnect(self): pass
        def publish(self, topic, payload, qos=0, retain=False):
            sent.append((topic, payload, retain))
            return Result()

    fake = types.ModuleType('paho.mqtt.client')
    fake.Client = Client
    fake.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    monkeypatch.setitem(sys.modules, 'paho', types.ModuleType('paho'))
    monkeypatch.setitem(sys.modules, 'paho.mqtt', types.ModuleType('paho.mqtt'))
    monkeypatch.setitem(sys.modules, 'paho.mqtt.client', fake)
    assert mqtt_status.remove({'host': 'h'}, 'lakeside') is True
    assert len(sent) == len(mqtt_status.ENTITIES) + 1 and all(p == '' and r for _, p, r in sent)
    assert ('homeassistant/binary_sensor/linznetz_energy_lakeside/portal_login_problem/config', '', True) in sent


# -- Live meter picked on the add-on page ------------------------------------------------------------

def test_live_meter_candidates_are_upward_energy_counters():
    candidates = addon.live_meter_candidates(FakeHass())
    assert [c['entity_id'] for c in candidates] == ['sensor.place_grid_total_energy_consumption', 'sensor.plug_energy']
    assert candidates[0] == {'entity_id': 'sensor.place_grid_total_energy_consumption',
                             'name': 'Place grid Total energy consumption', 'state': '12345.678', 'unit': 'kWh'}


def test_live_meter_choice_is_kept_in_the_state(tmp_path):
    addon.web.write_status(tmp_path, live_meter_request={'entity': 'sensor.place_grid_total_energy_consumption'})
    addon.handle_live_meter_request(FakeHass(), tmp_path)
    assert addon.read_state(tmp_path)['live_meter'] == 'sensor.place_grid_total_energy_consumption'
    status = addon.web.read_status(tmp_path)
    assert status['live_meter_result']['state'] == 'saved' and status['live_meter_request'] is None
    addon.web.write_status(tmp_path, live_meter_request={'entity': ''})                  # back to portal only
    addon.handle_live_meter_request(FakeHass(), tmp_path)
    assert addon.read_state(tmp_path)['live_meter'] == ''


def test_live_meter_choice_must_be_a_candidate(tmp_path):
    addon.web.write_status(tmp_path, live_meter_request={'entity': 'sensor.place_grid_power'})
    addon.handle_live_meter_request(FakeHass(), tmp_path)
    assert 'live_meter' not in addon.read_state(tmp_path)
    assert addon.web.read_status(tmp_path)['live_meter_result']['state'] == 'unknown'


def test_the_fetch_button_fetches_even_when_not_due(tmp_path, monkeypatch):
    fetched = []
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: fetched.append(a))
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    write_readings(tmp_path, hours=2)
    addon.write_state(tmp_path, {'portal_ok': NOW.isoformat(), 'portal_tried': NOW.isoformat()})   # just now
    addon.run_once(FakeHass(), options(), tmp_path, 'Europe/Vienna', NOW)
    assert fetched == []
    addon.run_once(FakeHass(), options(), tmp_path, 'Europe/Vienna', NOW, fetch_now=True)
    assert len(fetched) == 1


def test_another_live_meter_starts_a_new_archive(tmp_path):
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    hass = FakeHassWithHistory(live_states(start, 3))
    addon.update_live(hass, options(live_meter_entity='sensor.a'), tmp_path, start + timedelta(hours=3))
    assert addon.livearchive.read_hourly(tmp_path, METER)
    hass = FakeHassWithHistory([])
    addon.update_live(hass, options(live_meter_entity='sensor.b'), tmp_path, start + timedelta(hours=4))
    assert addon.livearchive.read_hourly(tmp_path, METER) == {} and addon.livearchive.read_raw(tmp_path, METER) == {}
    assert addon.read_state(tmp_path)['live_meter_entity'] == 'sensor.b'


def test_apparmor_profile_is_named_after_the_slug():
    folder = Path(addon.__file__).resolve().parent.parent
    slug = next(l.split(':', 1)[1].strip() for l in (folder / 'config.yaml').read_text().splitlines() if l.startswith('slug:'))
    profile = (folder / 'apparmor.txt').read_text()
    assert f'profile {slug} flags=(attach_disconnected,mediate_deleted) {{' in profile     # enforced, no complain



def test_a_plain_text_password_is_warned_about_once(tmp_path, monkeypatch):
    stored = {'portal_password': 'plain'}
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'x')
    monkeypatch.setattr(addon, 'stored_options', lambda: dict(stored))
    hass = FakeHass()
    addon.check_password_secret(hass, tmp_path, 'de')
    addon.check_password_secret(hass, tmp_path, 'de')
    notes = [s[2] for s in hass.services if s[2].get('notification_id') == addon.SECRET_NOTIFICATION]
    assert len(notes) == 1 and '**In YAML bearbeiten**' in notes[0]['message'] and '6. Tab **Info**' in notes[0]['message']
    assert addon.web.read_status(tmp_path)['password_secret'] is False
    stored['portal_password'] = '!secret linznetz_password'
    addon.check_password_secret(hass, tmp_path, 'de')
    assert hass.services[-1][1] == 'dismiss' and addon.web.read_status(tmp_path)['password_secret'] is True


def test_no_password_check_outside_home_assistant(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    hass = FakeHass()
    addon.check_password_secret(hass, tmp_path, 'en')
    assert hass.services == [] and addon.web.read_status(tmp_path)['password_secret'] is None


def test_statistic_gaps_are_listed(tmp_path):
    start = datetime(2023, 8, 31, 22, tzinfo=ZONE).astimezone(UTC)
    write_reconstructed(tmp_path, start, hours=2)                       # 31.08. 22:00 to 24:00
    write_readings(tmp_path, hours=2, start=datetime(2023, 10, 1, tzinfo=ZONE).astimezone(UTC))
    hours = addon.Inputs.read(tmp_path, METER).fused()
    status = addon.statistic_status('x', hours, ZONE)
    assert status['gaps'] == [{'from': '01.09.2023 00:00', 'to': '01.10.2023 00:00', 'hours': 720}]
    assert addon.statistic_gaps(hours[:2]) == []                          # the time after the newest hour is no gap


def test_an_unexpected_error_does_not_stop_the_add_on(tmp_path, monkeypatch):
    options_file = tmp_path / 'options.json'
    options_file.write_text(json.dumps({'portal_username': 'u', 'portal_password': 'p', 'metering_point': METER}))
    monkeypatch.setenv('ADDON_OPTIONS', str(options_file))
    monkeypatch.setenv('ADDON_STORE', str(tmp_path))
    monkeypatch.setattr(addon.web, 'start', lambda *a: None)
    monkeypatch.setattr(addon, 'announce_setup', lambda *a: None)
    monkeypatch.setattr(addon, 'next_run', lambda now: now)        # no waiting between runs
    calls = []

    def connect():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('boom')
        raise KeyboardInterrupt   # ends the test after the second run
    monkeypatch.setattr(addon, 'connect', connect)
    with pytest.raises(KeyboardInterrupt):
        addon.main([])
    assert len(calls) == 2                                          # it went on after the error
    assert addon.web.read_status(tmp_path)['detail'] == 'RuntimeError: boom'


def test_the_log_shows_only_the_end_of_the_metering_point(caplog):
    import logging
    handler = logging.StreamHandler()
    stream = __import__('io').StringIO()
    handler.setStream(stream)
    handler.addFilter(addon.MaskMeteringPoint('AT0099000000000000000000000001234'))
    log = logging.getLogger('test.mask')
    log.addHandler(handler)
    log.warning('Uploaded %s, importing', 'AT0099000000000000000000000001234_readings.csv')
    log.removeHandler(handler)
    assert stream.getvalue() == 'Uploaded …1234_readings.csv, importing\n'



def test_the_power_sensor_of_the_meter_device_is_found():
    hass = FakeHass()
    hass.registry = {e: {'device_id': 'meter'} for e in (
        'sensor.place_grid_total_energy_consumption', 'sensor.place_grid_current_power_consumption',
        'sensor.place_grid_current_power_production')}
    hass.registry['sensor.kitchen_power'] = {'device_id': 'plug'}
    power = {'unit_of_measurement': 'kW', 'device_class': 'power', 'state_class': 'measurement'}
    hass.states_list = hass.states_list + [
        {'entity_id': 'sensor.place_grid_current_power_consumption', 'state': '0.4', 'attributes': power},
        {'entity_id': 'sensor.place_grid_current_power_production', 'state': '0', 'attributes': power},
        {'entity_id': 'sensor.kitchen_power', 'state': '50', 'attributes': {**power, 'unit_of_measurement': 'W'}}]
    assert addon.power_entity(hass, 'sensor.place_grid_total_energy_consumption') == \
        ('sensor.place_grid_current_power_consumption', 1000.0)
    assert addon.power_entity(hass, 'sensor.unknown') is None                      # not in the registry


def test_live_hours_across_a_gap_are_split_by_the_power(tmp_path):
    start = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    counter = [(start + timedelta(minutes=m), 12_000_000 + (300 * m // 60 if m <= 60 else 300 + 1500 * (m - 60) // 60))
               for m in range(0, 121)]                                          # 300 W, then 1500 W from 06:00
    gap = [(t, v) for t, v in counter if not (start + timedelta(minutes=40) < t < start + timedelta(minutes=80))]
    states = [(t, f'{v / 1000:.3f}') for t, v in gap]
    power = [(start + timedelta(minutes=m), f'{0.3 if m < 60 else 1.5}') for m in range(0, 121)]

    class Hass(FakeHassWithHistory):
        def history(self, entity_id, since, end=None):
            return [(t, s) for t, s in (power if 'power' in entity_id else self.states) if t >= since]
    hass = Hass(states)
    hass.registry = {'sensor.place_grid_total_energy_consumption': {'device_id': 'm'},
                     'sensor.place_grid_current_power_consumption': {'device_id': 'm'}}
    hass.states_list = hass.states_list + [{'entity_id': 'sensor.place_grid_current_power_consumption', 'state': '1.5',
                                            'attributes': {'unit_of_measurement': 'kW', 'device_class': 'power'}}]
    o = options(live_meter_entity='sensor.place_grid_total_energy_consumption')
    summary = addon.update_live(hass, o, tmp_path, start + timedelta(hours=2))
    hourly = addon.livearchive.read_hourly(tmp_path, METER)
    assert hourly == {start: 300, start + timedelta(hours=1): 1500}               # an even split would give 700 / 1100
    assert summary['estimated'] == 0 and addon.livearchive.read_estimated(tmp_path, METER) == set()
    assert addon.web.read_status(tmp_path)['live_power'] == 'sensor.place_grid_current_power_consumption'


# -- Costs ------------------------------------------------------------------------------------------

LINZ_METER = 'AT0031000000000000000000000001234'      # made-up, LINZ NETZ operator code


def test_costs_need_a_supplier_and_then_follow_the_prices(tmp_path, monkeypatch):
    import prices
    from datetime import date
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    o = options(metering_point=LINZ_METER)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=24, start=start, name=f'{LINZ_METER}_readings.csv')   # 0.4 kWh per hour
    hass = FakeHass()
    monkeypatch.setattr(prices, 'http_get', lambda url: (_ for _ in ()).throw(prices.requests.RequestException('offline')))
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', NOW)
    assert not [m for m, _ in hass.imports if m['statistic_id'].endswith('_grid_cost')]      # no supplier, no costs
    # a fixed price contract from 1.10.: costs right away
    addon.web.write_status(tmp_path, contract_request={'supplier': 'fixed', 'start': '2026-10-01',
                                                       'energy_ct_kwh': '20', 'base_fee_month': '0'})
    addon.handle_contract_request(tmp_path)
    assert addon.web.read_status(tmp_path)['contract_result'] == {'state': 'calculating'}   # the page keeps refreshing
    hass = FakeHass()
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert addon.web.read_status(tmp_path)['contract_result'] == {'state': 'saved'}         # until the costs are there
    cost = [(m, s) for m, s in hass.imports if m['statistic_id'] == 'linznetz_energy:place_grid_cost']
    assert cost and cost[0][0]['unit_of_measurement'] == 'EUR' and cost[0][0]['name'] == 'Place electricity cost'
    stats = [s for _, chunk in cost for s in chunk]
    first_day = [s for s in stats if s['start'] < start + timedelta(days=1)]
    # 24 hours x 0.4 kWh x (20 ct energy + LINZ NETZ 2026: 5.57 + 0.487 + 0.1 + 0.583 + 0.037 ct) x 1.2 VAT,
    # plus one day of the fixed grid fees (Leistungspreis 54, Pauschalen 19.02 + 3.796 EUR/year, Messentgelt 2.38 EUR/month)
    per_kwh = (20 + 5.57 + 0.487 + 0.1 + 0.583 + 0.037) / 100
    fixed_day = (54 + 19.02 + 3.796) / 365 + 2.38 / 31
    assert first_day[-1]['sum'] == pytest.approx((24 * 0.4 * per_kwh + fixed_day) * 1.2, abs=0.001)
    status = addon.web.read_status(tmp_path)['costs']
    assert status['grid'] == 'LINZ NETZ' and status['contract']['supplier'] == 'Fixed price' and status['energy_ct'] == 20.0


def test_price_sensors_get_the_current_price_and_their_history(tmp_path, monkeypatch):
    import prices
    published = []
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: {'host': 'core-mosquitto'})
    monkeypatch.setattr(addon.mqtt_status, 'publish', lambda *a: published.append(a[-1]) or True)
    monkeypatch.setattr(prices, 'http_get', lambda url: (_ for _ in ()).throw(prices.requests.RequestException('offline')))
    o = options(metering_point=LINZ_METER)
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    write_readings(tmp_path, hours=24, start=start, name=f'{LINZ_METER}_readings.csv')
    addon.web.write_status(tmp_path, contract_request={'supplier': 'fixed', 'start': '2026-10-01',
                                                       'energy_ct_kwh': '20', 'base_fee_month': '0'})
    addon.handle_contract_request(tmp_path)
    hass = FakeHass()
    hass.registry = {'sensor.place_linznetz_price_grid': {'platform': 'mqtt', 'unique_id': 'linznetz_energy_place_price_grid'},
                     'sensor.renamed_total': {'platform': 'mqtt', 'unique_id': 'linznetz_energy_place_price_total'},
                     'sensor.other': {'platform': 'mqtt', 'unique_id': 'something_else'}}
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', NOW)
    grid = (5.57 + 0.487 + 0.1 + 0.583 + 0.037) * 1.2                       # LINZ NETZ 2026, October: no summer rate
    assert published[-1]['price_energy'] == pytest.approx(24.0)
    assert published[-1]['price_grid'] == pytest.approx(grid) and published[-1]['price_total'] == pytest.approx(24 + grid)
    history = {m['statistic_id']: (m, s) for m, s in hass.imports if not m['statistic_id'].startswith('linznetz_energy:')}
    assert set(history) == {'sensor.place_linznetz_price_grid', 'sensor.renamed_total'}   # found by unique ID, energy not registered yet
    meta, stats = history['sensor.renamed_total']
    assert meta['source'] == 'recorder' and meta['mean_type'] == 1 and not meta['has_sum'] and meta['unit_class'] is None
    assert stats[0]['start'] == start and stats[-1]['start'] == datetime(2026, 10, 14, 11, tzinfo=UTC)   # up to the current hour
    assert stats[0]['mean'] == stats[0]['max'] == pytest.approx(24 + grid)
    # the next run only adds the new hour; a new price writes everything again
    hass.imports.clear()
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert [len(s) for m, s in hass.imports if m['source'] == 'recorder'] == [1, 1]
    addon.web.write_status(tmp_path, contract_request={'supplier': 'fixed', 'start': '2026-10-01',
                                                       'energy_ct_kwh': '18', 'base_fee_month': '0'})
    addon.handle_contract_request(tmp_path)
    hass.imports.clear()
    addon.run_once(hass, o, tmp_path, 'Europe/Vienna', NOW + timedelta(hours=2))
    totals = [s for m, s in hass.imports if m['statistic_id'] == 'sensor.renamed_total'][0]
    assert len(totals) > 300 and totals[0]['mean'] == pytest.approx(18 * 1.2 + grid)


def test_statistics_say_mean_type_and_unit_class(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    write_readings(tmp_path, hours=2)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    meta = hass.imports[0][0]
    assert meta['mean_type'] == 0 and meta['unit_class'] == 'energy' and meta['has_sum'] and 'has_mean' not in meta


def test_the_fixed_price_comparison_reads_the_offer_once_a_day(tmp_path, monkeypatch):
    import prices
    from datetime import date
    page = ('(Preise inklusive 20 % USt.) strom 21,00 Cent/kWh + 3,00 Euro pro Monat Grundpauschale mit Online-Bonus '
            'Fixer Arbeitspreis mit Preisgarantie bis 31.12.2028').encode()
    calls = []
    monkeypatch.setattr(prices, 'http_get', lambda url: calls.append(url) or page)
    state = {}
    hours = [(datetime(2026, 9, 1, tzinfo=ZONE).astimezone(UTC) + timedelta(hours=i), 400) for i in range(24 * 40)]
    assert prices.comparison(tmp_path, state, hours, [], NOW, ZONE) is None and not calls     # no contract: nothing fetched
    prices.set_contract(state, 'fixed', '', date(2026, 9, 1), {'energy_ct_kwh': 15.0, 'base_fee_month': 0.0})
    comps = prices.components(tmp_path, METER, state)
    result = prices.comparison(tmp_path, state, hours, comps, NOW, ZONE)
    assert calls == [prices.tariffs.GOGREEN_FIX_PAGE] and result['product'] == ''
    assert result['today']['ct'] == 21.0 and result['rows'][0]['month'] == '2026-09'
    assert (tmp_path / prices.OFFERS).read_text().splitlines()[1] == '2026-10-14;21;3;2028-12-31;page'
    prices.comparison(tmp_path, state, hours, comps, NOW + timedelta(hours=1), ZONE)
    assert len(calls) == 1                                                               # once a day


def test_go_green_prices_are_fetched_while_the_month_has_none(tmp_path):
    import prices
    from datetime import date
    state = {}
    prices.set_contract(state, 'gogreen_flex', 'strom flex future', date(2023, 3, 1), {})
    calls = []
    history = [{'fromDate': '2026-09-01', 'untilDate': '2026-09-30', 'energyPrice': 0.1726},
               {'fromDate': '2026-10-01', 'untilDate': '2100-12-31', 'energyPrice': 0.2032}]

    def get(url):
        calls.append(url)
        return json.dumps([{'key': 'GSFLEX', 'options': {'future': {'energyPrice': 0.005}}, 'history': history}]).encode()
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    assert prices.update(tmp_path, state, now, ZONE, get) is None and len(calls) == 1
    archive = {c.valid_from: c.value for c in prices.costs.read_archive(tmp_path / prices.ARCHIVE)}
    assert archive == {date(2026, 9, 1): 17.76, date(2026, 10, 1): 20.82}      # the past months come along
    prices.update(tmp_path, state, now + timedelta(hours=1), ZONE, get)
    assert len(calls) == 1                                   # the month has its price: no more fetches
    # November: not published yet, so retried at most every 6 hours
    nov = datetime(2026, 11, 1, 8, tzinfo=UTC)
    assert prices.update(tmp_path, state, nov, ZONE, get) == 'not published yet (latest: 10/2026)'
    prices.update(tmp_path, state, nov + timedelta(hours=1), ZONE, get)
    assert len(calls) == 2
    prices.update(tmp_path, state, nov + timedelta(hours=7), ZONE, get)
    assert len(calls) == 3

def test_a_price_list_is_imported_into_the_archive(tmp_path):
    import prices
    (tmp_path / 'import').mkdir()
    (tmp_path / 'import' / f'{METER}_prices.csv').write_text(
        'valid_from;valid_to;kind;component;unit;value;vat\n'
        '2024-04-01;2024-05-01;energy;Energiepreis;ct/kWh;8,00;0.2\n'
        '2024-07-01;2025-01-01;subsidy;Stromkostenzuschuss;ct/kWh;-2.00;0\n'
        'not;a;row\n')
    done = addon.import_history(tmp_path, METER)
    assert done == [('import.prices', {'file': f'{METER}_prices.csv', 'rows': 2, 'rejected': 1})]
    assert len(prices.costs.read_archive(tmp_path / prices.ARCHIVE)) == 2
    (tmp_path / 'import' / f'{METER}_prices.csv').write_text('a;b\n1;2\n')
    assert addon.import_history(tmp_path, METER)[0][1]['reason_key'] == 'import.bad_header'


def test_a_new_supplier_ends_the_previous_one():
    import prices
    from datetime import date
    state = {}
    prices.set_contract(state, 'gogreen_flex', 'strom flex future', date(2023, 3, 1), {})
    prices.set_contract(state, 'fixed', '', date(2026, 11, 1), {'energy_ct_kwh': 15.0, 'base_fee_month': 3.0})
    first, second = prices.contracts(state)
    assert first['end'] == date(2026, 11, 1) and second['end'] is None
    comps = prices.components(Path('/nonexistent'), LINZ_METER, state)
    flex_fee = [c for c in comps if c.name == 'Online-Bonus']
    assert flex_fee[0].valid_to == date(2026, 11, 1)
    assert {c.source for c in comps if c.kind == 'grid'} == {'grid'}


def test_an_invalid_contract_is_not_saved(tmp_path):
    addon.web.write_status(tmp_path, contract_request={'supplier': 'gogreen_flex', 'product': 'gas', 'start': '2026-10-01'})
    addon.handle_contract_request(tmp_path)
    assert addon.web.read_status(tmp_path)['contract_result'] == {'state': 'invalid'}
    assert 'contracts' not in addon.read_state(tmp_path)


def test_writing_the_costs_keeps_what_the_live_update_stored(tmp_path, monkeypatch):
    """The run's state copy is older than update_live's change of the live meter; writing the
    costs must not bring the old entity back (the live archive would be cleared every hour)."""
    import prices
    monkeypatch.setattr(prices, 'http_get', lambda url: (_ for _ in ()).throw(prices.requests.RequestException('offline')))
    addon.write_state(tmp_path, {'live_meter_entity': 'sensor.old'})
    stale = addon.read_state(tmp_path)
    addon.write_state(tmp_path, {'live_meter_entity': 'sensor.new'})              # what update_live stored
    addon.write_costs(FakeHass(), options(), tmp_path, stale, [], ZONE, NOW, 'en')
    assert addon.read_state(tmp_path)['live_meter_entity'] == 'sensor.new'


def test_a_contract_with_a_non_number_is_not_saved(tmp_path):
    addon.web.write_status(tmp_path, contract_request={'supplier': 'fixed', 'start': '2026-11-01',
                                                       'energy_ct_kwh': 'nan', 'base_fee_month': '3'})
    addon.handle_contract_request(tmp_path)
    assert addon.web.read_status(tmp_path)['contract_result'] == {'state': 'invalid'}



def test_a_price_list_needs_no_metering_point_in_its_name(tmp_path):
    (tmp_path / 'import').mkdir()
    (tmp_path / 'import' / 'prices.csv').write_text(
        'valid_from;valid_to;kind;component;unit;value;vat\n2024-04-01;2024-05-01;energy;Energiepreis;ct/kWh;8;0.2\n')
    assert addon.import_history(tmp_path, METER) == [('import.prices', {'file': 'prices.csv', 'rows': 1, 'rejected': 0})]


def test_a_price_list_uploaded_in_the_card_reports_back_there(tmp_path, monkeypatch):
    monkeypatch.setattr(addon, 'fetch_portal', lambda *a: None)
    monkeypatch.setattr(addon.mqtt_status, 'broker', lambda: None)
    write_readings(tmp_path, hours=2)
    (tmp_path / 'import').mkdir()
    (tmp_path / 'import' / 'prices.csv').write_text(
        'valid_from;valid_to;kind;component;unit;value;vat\n2026-10-01;2026-11-01;energy;Energiepreis;ct/kWh;20;0.2\nbad\n')
    addon.web.write_status(tmp_path, prices_import={'state': 'importing'})
    addon.run_once(FakeHass(), options(), tmp_path, 'Europe/Vienna', NOW)
    assert addon.web.read_status(tmp_path)['prices_import'] == {
        'state': 'done', 'lines': [['import.prices', {'file': 'prices.csv', 'rows': 1, 'rejected': 1}]]}


def test_grid_charges_are_checked_online_and_a_missing_year_is_reported(tmp_path, monkeypatch):
    import prices
    from datetime import date
    from test_costs import SNE5, SNE6, ris_json
    pages = {'p5': SNE5.encode(), 'p6': SNE6.encode()}
    calls = []

    def get(url):
        calls.append(url)
        return pages.get(url) or ris_json()
    state = {}
    now = datetime(2026, 10, 16, 8, tzinfo=UTC)
    assert prices.update_grid(tmp_path, state, now, ZONE, LINZ_METER, get) == {'state': 'online'}
    assert {c.source for c in prices.costs.read_archive(tmp_path / prices.ARCHIVE)} == {'ris:SNE-V'}
    n = len(calls)
    prices.update_grid(tmp_path, state, now + timedelta(hours=1), ZONE, LINZ_METER, get)
    assert len(calls) == n                                                   # once a day
    offline = lambda url: (_ for _ in ()).throw(prices.requests.RequestException('offline'))   # noqa: E731
    later = datetime(2026, 10, 25, 8, tzinfo=UTC)
    assert prices.update_grid(tmp_path, state, later, ZONE, LINZ_METER, offline)['state'] == 'table'
    # a year neither the table nor the RIS has: warn once, in HA too
    monkeypatch.setattr(prices, 'http_get', offline)
    o = options(metering_point=LINZ_METER)
    addon.write_state(tmp_path, {'grid_confirmed': '2026-10-16', 'grid_check': '2027-01-05T08:00:00+00:00'})
    prices.set_contract(st := addon.read_state(tmp_path), 'fixed', '', date(2026, 10, 1), {'energy_ct_kwh': 20})
    addon.write_state(tmp_path, st)
    from types import SimpleNamespace
    hours = [SimpleNamespace(start=datetime(2027, 1, 5, 7, tzinfo=UTC), wh=400)]
    hass = FakeHass()
    now27 = datetime(2027, 1, 5, 8, tzinfo=UTC)
    addon.write_costs(hass, o, tmp_path, {}, hours, ZONE, now27, 'en')
    assert addon.web.read_status(tmp_path)['costs']['grid_status']['state'] == 'outdated'
    created = [c for c in hass.services if c[1] == 'create' and c[2]['notification_id'] == addon.GRID_NOTIFICATION]
    assert len(created) == 1 and '2027' in created[0][2]['title']
    addon.write_costs(hass, o, tmp_path, {}, hours, ZONE, now27 + timedelta(hours=1), 'en')
    assert len([c for c in hass.services if c[1] == 'create' and c[2]['notification_id'] == addon.GRID_NOTIFICATION]) == 1
    # the add-on update brings the year (here: the RIS answers again): the notification goes away
    monkeypatch.setattr(prices, 'http_get', get)
    addon.write_costs(hass, o, tmp_path, {}, hours, ZONE, now27 + timedelta(days=1), 'en')
    assert [c for c in hass.services if c[1] == 'dismiss' and c[2]['notification_id'] == addon.GRID_NOTIFICATION]


def test_supplier_fees_count_per_period_and_a_price_list_wins(tmp_path):
    import prices
    from datetime import date
    C = prices.costs.Component
    state = {}
    prices.set_contract(state, 'gogreen_flex', 'strom flex future', date(2023, 3, 1), {'online_bonus_month': 0})
    prices.set_contract(state, 'fixed', '', date(2027, 6, 1), {'energy_ct_kwh': 15.0})
    src = 'gogreen_flex:api'
    prices.costs.write_archive(tmp_path / prices.ARCHIVE, [
        C('energy', 'Grundpauschale', 'EUR/month', 3.0, 0.2, date(2026, 11, 1), None, src),
        C('energy', 'Online-Bonus', 'EUR/month', -0.9, 0.2, date(2026, 11, 1), None, src),     # the contract has no bonus
        C('energy', 'Grundpauschale', 'EUR/month', 2.0, 0.2, date(2027, 1, 1), date(2027, 2, 1), 'import'),
    ])
    comps = prices.components(tmp_path, LINZ_METER, state)
    fee = lambda day: {c.name: c.value for c in prices.costs.effective(comps, day) if c.unit == 'EUR/month' and c.kind == 'energy'}   # noqa: E731
    assert fee(date(2026, 10, 1)) == {'Grundpauschale': 2.5, 'Online-Bonus': 0.0}          # the contract's values
    assert fee(date(2026, 12, 1)) == {'Grundpauschale': 3.0, 'Online-Bonus': 0.0}          # what go green published
    assert fee(date(2027, 1, 15))['Grundpauschale'] == 2.0                                 # the price list wins
    assert fee(date(2027, 7, 1)) == {'Grundpauschale': 0.0}                                # the next contract's own fee


def test_an_unchanged_fee_starts_no_new_period(tmp_path):
    import prices
    from datetime import date
    state = {}
    prices.set_contract(state, 'gogreen_flex', 'strom flex', date(2023, 3, 1), {})

    def get(month):
        return lambda url: json.dumps([{'key': 'GSFLEX', 'basicCharge': 2.5, 'options': {'OnlineBonus': {'basicCharge': -0.833333}},
                                        'history': [{'fromDate': f'2026-{month:02d}-01', 'untilDate': '2100-12-31',
                                                     'energyPrice': 0.2}]}]).encode()
    for month in (10, 11, 12):
        prices.update(tmp_path, state, datetime(2026, month, 2, 8, tzinfo=UTC), ZONE, get(month))
    fees = [c for c in prices.costs.read_archive(tmp_path / prices.ARCHIVE) if c.unit == 'EUR/month']
    assert sorted((c.name, c.valid_from) for c in fees) == [('Grundpauschale', date(2026, 10, 1)),
                                                          ('Online-Bonus', date(2026, 10, 1))]
