"""The fuse command on files: the live counter is primary, the portal verifies, repeatable output."""
import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import databot
from homebase import fusion as F

ZONE = ZoneInfo('Europe/Vienna')
UTC = ZoneInfo('UTC')
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
COUNTER = 12_345_000   # synthetic counter start in Wh


def counter(minutes: int, step_minutes: int = 1, watts: int = 600, start=T0, base=COUNTER) -> list:
    return [(start + timedelta(minutes=m), base + round(watts * m / 60))
            for m in range(0, minutes + 1, step_minutes)]


def test_fuse_writes_a_repeatable_file(tmp_path, monkeypatch):
    meter = 'AT0000000000000000000000000000000'
    with (tmp_path / f'{meter}_readings.csv').open('w', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['start_local', 'start_utc', 'kwh', 'kw', 'substitute'])
        for i in range(8):
            q = T0 + i * F.QUARTER
            w.writerow([q.astimezone(ZONE).isoformat(), q.strftime('%Y-%m-%dT%H:%M:%SZ'), '0.150', '0.600', 'false'])
    history = tmp_path / 'history.csv'
    with history.open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['entity_id', 'state', 'last_changed'])
        for t, v in counter(240):
            w.writerow(['sensor.place_grid_energy', f'{v / 1000:.3f}', t.isoformat()])
    monkeypatch.setenv('METER_ID', meter)
    monkeypatch.setenv('TZ', 'Europe/Vienna')
    monkeypatch.setenv('EXPORT_DIR', str(tmp_path))
    args = ['fuse', '--live', str(history)]
    assert databot.main(args) == 0
    out = tmp_path / f'{meter}_fused_1h.csv'
    first = out.read_bytes()
    assert databot.main(args) == 0 and out.read_bytes() == first
    rows = list(csv.DictReader(out.open(), delimiter=';'))
    assert [r['source'] for r in rows] == ['live', 'live', 'live', 'live']   # the live counter is primary
    assert [r['portal_kwh'] for r in rows] == ['0.600', '0.600', '', '']   # the portal verifies
    assert [r['flag'] for r in rows] == [''] * 4
    assert [r['sum_kwh'] for r in rows] == ['0.600', '1.200', '1.800', '2.400']
