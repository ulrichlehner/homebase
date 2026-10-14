"""The disaggregate command on files: repeatable output, inputs untouched, unified file."""
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databot
from homebase import disaggregate as D
from homebase import readings

TZ = 'Europe/Vienna'
ZONE = ZoneInfo(TZ)
UTC = ZoneInfo('UTC')


def run(monkeypatch, folder: Path, meter: str, args: list) -> int:
    monkeypatch.setenv('METER_ID', meter)
    monkeypatch.setenv('TZ', TZ)
    monkeypatch.setenv('EXPORT_DIR', str(folder))
    return databot.main(['disaggregate', *args])


def local(*args, fold=0):
    return datetime(*args, tzinfo=ZONE, fold=fold)


def synthetic_readings(date_from: date, date_to: date) -> dict:
    """Made-up household: 0.05 kWh base load per quarter hour, 0.2 in the evening
    (18:00 to 21:00), weekends 0.1 in the morning (08:00 to 11:00)."""
    out = {}
    t = local(date_from.year, date_from.month, date_from.day).astimezone(UTC)
    stop = local(date_to.year, date_to.month, date_to.day).astimezone(UTC) + timedelta(days=1)
    while t < stop:
        lt = t.astimezone(ZONE)
        kwh = 0.05
        if 18 <= lt.hour < 21:
            kwh = 0.2
        elif 8 <= lt.hour < 11 and D.day_type(lt.date()) == 'weekend':
            kwh = 0.1
        out[t] = kwh
        t += D.QUARTER
    return out


def write_inputs(folder: Path, meter: str) -> None:
    measured = synthetic_readings(date(2023, 9, 1), date(2024, 8, 31))
    with (folder / f'{meter}_readings.csv').open('w', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(readings.CSV_HEADER)
        for t in sorted(measured):
            w.writerow([t.astimezone(ZONE).isoformat(), t.strftime('%Y-%m-%dT%H:%M:%SZ'),
                        f'{measured[t]:.3f}', f'{4 * measured[t]:.3f}', 'false'])
    with (folder / f'{meter}_reconstructed_6h.csv').open('w', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['start_local', 'end_local', 'kwh', 'source', 'method', 'note'])
        start = local(2023, 8, 30, 0, 15)
        for n in range(8):   # 2023-08-30 00:15 to 2023-09-01 00:15
            s = start + timedelta(hours=6 * n)
            w.writerow([s.isoformat(), (s + timedelta(hours=6)).isoformat(), '1.20', 'chart_ocr', 'label',
                        'value label read from chart by OCR'])


def test_main_is_repeatable_and_leaves_inputs_alone(tmp_path, monkeypatch):
    meter = 'AT0000000000000000000000000000000'
    write_inputs(tmp_path, meter)
    inputs = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = ['--profile-from', '2023-09-01',
            '--profile-to', '2024-08-31', '--backtest-from', '2024-03-01', '--backtest-to', '2024-03-31']
    assert run(monkeypatch, tmp_path, meter, args) == 0
    out = tmp_path / f'{meter}_reconstructed_15min.csv'
    first = out.read_bytes()
    assert run(monkeypatch, tmp_path, meter, args) == 0
    assert out.read_bytes() == first   # byte-identical
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p != out} == inputs

    rows = list(csv.DictReader(out.open(), delimiter=';'))
    assert len(rows) == 8 * 24
    assert round(sum(float(r['kwh']) for r in rows), 3) == 9.6
    assert {r['method'] for r in rows} == {'profile'}
    assert all(',' not in line for line in out.read_text().splitlines())


def test_backtest_only_writes_nothing(tmp_path, monkeypatch):
    meter = 'AT0000000000000000000000000000000'
    write_inputs(tmp_path, meter)
    assert run(monkeypatch, tmp_path, meter, ['--backtest', '--backtest-from', '2024-03-01',
                   '--backtest-to', '2024-03-31']) == 0
    assert not (tmp_path / f'{meter}_reconstructed_15min.csv').exists()


def test_unified_file_prefers_the_quarter_hours(tmp_path, monkeypatch):
    meter = 'AT0000000000000000000000000000000'
    write_inputs(tmp_path, meter)
    rows = readings.read_csv_file(tmp_path / f'{meter}_readings.csv')
    unified = tmp_path / f'{meter}_all.csv'

    readings.write_unified(tmp_path, meter, TZ, rows, unified)
    blocks = [r for r in csv.DictReader(unified.open(), delimiter=';') if r['source'] == 'chart_ocr']
    assert len(blocks) == 8 and blocks[-1]['end_local'] == '2023-09-01T00:00:00+02:00'   # trimmed

    assert run(monkeypatch, tmp_path, meter, ['--profile-from', '2023-09-01',
                   '--profile-to', '2024-08-31', '--backtest-from', '2024-03-01', '--backtest-to', '2024-03-31']) == 0
    readings.write_unified(tmp_path, meter, TZ, rows, unified)
    all_rows = list(csv.DictReader(unified.open(), delimiter=';'))
    recon = [r for r in all_rows if r['source'] == 'chart_ocr']
    assert {r['method'] for r in recon} == {'profile'}
    assert len(recon) == 8 * 24 - 1   # the quarter hour from 00:00 on 1 September is measured
    assert recon[-1]['start_local'] == '2023-08-31T23:45:00+02:00'
    assert all_rows[len(recon)]['source'] == 'portal'


# -- Portal client: an expired session is retried with a fresh login --------------
