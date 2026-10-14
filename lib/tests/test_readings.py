from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homebase import readings

TZ = 'Europe/Vienna'


def test_chunks_forward_and_backward():
    fwd = list(readings.chunks(date(2024, 1, 1), date(2024, 1, 10), 4))
    assert fwd == [(date(2024, 1, 1), date(2024, 1, 4)), (date(2024, 1, 5), date(2024, 1, 8)),
                   (date(2024, 1, 9), date(2024, 1, 10))]
    back = list(readings.chunks(date(2024, 1, 1), date(2024, 1, 10), 4, backwards=True))
    assert back == [(date(2024, 1, 7), date(2024, 1, 10)), (date(2024, 1, 3), date(2024, 1, 6)),
                    (date(2024, 1, 1), date(2024, 1, 2))]


def test_group_days():
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4)]
    assert readings.group_days(days) == [(date(2024, 1, 1), date(2024, 1, 2)), (date(2024, 1, 4), date(2024, 1, 4))]


def test_csv_file_round_trip(tmp_path):
    from homebase.portal import Reading
    z = ZoneInfo(TZ)
    path = tmp_path / 'readings.csv'
    start = datetime(2024, 1, 1, 0, 0, tzinfo=z)
    rs = [Reading(start=start + timedelta(minutes=15 * i), end=start + timedelta(minutes=15 * (i + 1)),
                        kwh=0.1 * i, substitute=(i == 1)) for i in range(3)]
    rows = readings.readings_to_rows(rs, {rs[0].start.timestamp(): 0.42})
    readings.write_csv_file(path, rows)
    loaded = readings.read_csv_file(path)
    assert list(loaded) == sorted(loaded) and len(loaded) == 3
    assert loaded[rs[0].start.timestamp()] == ['2024-01-01T00:00:00+01:00', '2023-12-31T23:00:00Z', '0.000', '0.420', 'false']
    assert loaded[rs[1].start.timestamp()][2:] == ['0.100', '0.400', 'true']  # kW derived when not supplied
    # merging newer data overwrites the same interval and keeps the rest
    newer = readings.readings_to_rows([Reading(start=rs[2].start, end=rs[2].end, kwh=9.0, substitute=False)], {})
    loaded.update(newer)
    readings.write_csv_file(path, loaded)
    again = readings.read_csv_file(path)
    assert len(again) == 3 and again[rs[2].start.timestamp()][2] == '9.000'


def test_incomplete_days_in_rows():
    from homebase.portal import Reading
    z = ZoneInfo(TZ)
    rows = {}
    for day, n in ((date(2024, 1, 1), 96), (date(2024, 1, 2), 80), (date(2024, 1, 4), 96)):  # 3rd missing entirely
        start = datetime.combine(day, datetime.min.time(), tzinfo=z)
        for i in range(n):
            t = start + timedelta(minutes=15 * i)
            rows[t.timestamp()] = [t.isoformat(), '', '0.1', '0.4', 'false']
    assert readings.incomplete_days_in_rows(rows, TZ, date(2024, 1, 4)) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert readings.incomplete_days_in_rows({}, TZ, date(2024, 1, 4)) == []


def test_write_unified_trims_boundary(tmp_path):
    z = ZoneInfo(TZ)
    # reconstructed block 18:15-00:15 overlapping the first measured reading at 00:00
    (tmp_path / 'M_reconstructed_6h.csv').write_text(
        'start_local;end_local;kwh;source;method;note\n'
        '2023-08-31T18:15:00+02:00;2023-09-01T00:15:00+02:00;1.13;chart_ocr;label;read from chart\n')
    rows = {}
    for i in range(3):
        t = datetime(2023, 9, 1, 0, 15 * i, tzinfo=z)
        rows[t.timestamp()] = [t.isoformat(), '', '0.027', '0.108', 'true' if i == 1 else 'false']
    out = tmp_path / 'M_all.csv'
    assert readings.write_unified(tmp_path, 'M', TZ, rows, out) == 4
    lines = list(__import__('csv').DictReader(out.open(), delimiter=';'))
    assert lines[0]['end_local'] == '2023-09-01T00:00:00+02:00' and lines[0]['kwh'] == '1.103'
    assert 'trimmed' in lines[0]['note'] and lines[0]['source'] == 'chart_ocr'
    assert lines[1]['method'] == 'measured' and lines[2]['method'] == 'substitute'
    assert all(a['end_local'] == b['start_local'] for a, b in zip(lines, lines[1:]))
    assert not any(',' in v for l in lines for v in l.values())
