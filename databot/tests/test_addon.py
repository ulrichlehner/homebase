import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import addon  # noqa: E402
import linznetz  # noqa: E402

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


def write_readings(store: Path, hours: int):
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    with (store / f'{METER}_readings.csv').open('w') as fh:
        fh.write('start_local;start_utc;kwh;kw;substitute\n')
        for i in range(hours * 4):
            q = start + timedelta(minutes=15 * i)
            fh.write(f'{q.astimezone(ZONE).isoformat()};{q.strftime("%Y-%m-%dT%H:%M:%SZ")};0.100;0.400;false\n')


def test_load_options(tmp_path):
    path = tmp_path / 'options.json'
    path.write_text(json.dumps({'username': 'u', 'password': 'p', 'meter_id': f' {METER} ', 'site': 'Place_1'}))
    o = addon.load_options(path)
    assert o.meter_id == METER and o.site == 'place_1' and o.portal_hours == 12
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
    assert [len(stats) for _, stats in hass.imports] == [5, 5, 2]
    metadata = hass.imports[0][0]
    assert metadata['statistic_id'] == 'linznetz:place_grid_energy_portal' and metadata['source'] == 'linznetz'
    assert metadata['name'] == 'Place grid energy (portal)'
    last = hass.imports[-1][1][-1]
    assert last['sum'] == pytest.approx(4.8) and last['start'].minute == 0
    assert ('persistent_notification', 'dismiss', {'notification_id': addon.LOGIN_NOTIFICATION}) in hass.services
    assert 'portal_ok' in addon.read_state(tmp_path)
    # the next hour: no portal fetch, the statistic is imported again
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW + timedelta(hours=1))
    assert len(fetched) == 1 and len(hass.imports) == 6


def test_rejected_login_notifies_and_keeps_going(tmp_path, monkeypatch, caplog):
    write_readings(tmp_path, hours=2)

    def reject(*a):
        raise linznetz.LoginError('LINZ NETZ rejected the credentials')
    monkeypatch.setattr(addon, 'fetch_portal', reject)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    created = [s for s in hass.services if s[1] == 'create']
    assert created and created[0][2]['notification_id'] == addon.LOGIN_NOTIFICATION
    assert len(hass.imports) == 1   # the existing readings are still imported
    assert 'portal_login_failed' in addon.read_state(tmp_path)
    assert 'secret-pw' not in caplog.text and 'secret-pw' not in json.dumps(hass.services)


def test_other_portal_errors_retry_next_hour(tmp_path, monkeypatch):
    def broken(*a):
        raise linznetz.LinzNetzError('giving up after 4 attempts')
    monkeypatch.setattr(addon, 'fetch_portal', broken)
    hass = FakeHass()
    addon.run_once(hass, options(), tmp_path, 'Europe/Vienna', NOW)
    assert hass.services == [] and hass.imports == []
    assert addon.portal_due(addon.read_state(tmp_path), NOW + timedelta(hours=1), 12)
