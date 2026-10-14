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
    return addon.Options(**{'username': 'u', 'password': 'secret-pw', 'meter_id': METER, 'site': 'place', **kw})


class FakeHass:
    def __init__(self):
        self.imports = []
        self.services = []

    def import_statistics(self, metadata, stats):
        self.imports.append((metadata, list(stats)))

    def call_service(self, domain, service, data=None):
        self.services.append((domain, service, data))


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
    path.write_text(json.dumps({'username': 'u', 'password': 'p', 'meter_id': f' {METER} ', 'site': 'Place_1'}))
    o = addon.load_options(path)
    assert o.meter_id == METER and o.site == 'place_1' and o.fetch_interval_hours == 12
    assert o.portal_statistic == 'linznetz:place_1_grid_energy_portal'
    assert o.title == 'Place 1'
    path.write_text(json.dumps({'username': 'u', 'password': '', 'meter_id': METER, 'site': 'x'}))
    with pytest.raises(SystemExit, match='password'):
        addon.load_options(path)


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
        [('linznetz:place_grid_energy_portal', 5)] * 2 + [('linznetz:place_grid_energy_portal', 2)] + \
        [('linznetz:place_grid_energy_fused', 5)] * 2 + [('linznetz:place_grid_energy_fused', 2)]
    metadata = hass.imports[0][0]
    assert metadata['source'] == 'linznetz' and metadata['name'] == 'Place grid energy (portal)'
    assert hass.imports[3][0]['name'] == 'Place grid energy (fused)'
    last = hass.imports[-1][1][-1]
    assert last['sum'] == pytest.approx(4.8) and last['start'].minute == 0
    assert ('persistent_notification', 'dismiss', {'notification_id': addon.LOGIN_NOTIFICATION}) in hass.services
    assert 'portal_ok' in addon.read_state(tmp_path)
    # the next hour: no portal fetch, the statistic is imported again
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert len(fetched) == 1 and len(hass.imports) == 12


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
    assert lines == [f'{METER}_readings.csv: 4 of 12 readings added']
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
    assert len(lines) == 3 and all('NOT imported' in line for line in lines)
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
    portal, fused = by_id['linznetz:place_grid_energy_portal'], by_id['linznetz:place_grid_energy_fused']
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
    assert [m['title'] for m in msgs] == ['LINZ NETZ energy: first download', 'LINZ NETZ energy: first download done']
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
    assert titles == ['LINZ NETZ energy: first download', 'LINZ NETZ energy: first download failed']
