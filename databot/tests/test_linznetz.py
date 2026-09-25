import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import databot  # noqa: E402
from linznetz import (  # noqa: E402
    ParseError,
    expected_intervals,
    extract_view_state,
    extract_view_state_from_partial,
    find_csv_button,
    find_unit_field,
    parse_csv,
    parse_initial_state,
)

TZ = 'Europe/Vienna'


def test_parse_csv_basic():
    content = (
        'Datum von;Datum bis;Energiemenge in kWh;Ersatzwert\n'
        '17.09.2022 00:00;17.09.2022 00:15;0,020;\n'
        '17.09.2022 00:15;17.09.2022 00:30;;0,010\n'
        '17.09.2022 00:30;17.09.2022 00:45;;\n'
        '17.09.2022 00:45;17.09.2022 01:00;1.234,500;\n'
    ).encode('utf-8-sig')
    readings = parse_csv(content, TZ)
    assert len(readings) == 3
    assert readings[0].start == datetime(2022, 9, 17, 0, 0, tzinfo=ZoneInfo(TZ))
    assert readings[0].end - readings[0].start == timedelta(minutes=15)
    assert readings[0].kwh == 0.02 and not readings[0].substitute
    assert readings[1].kwh == 0.01 and readings[1].substitute
    assert readings[2].kwh == 1234.5


def test_parse_csv_autumn_dst_switch():
    # 30.10.2022: 02:00 - 03:00 local occurs twice, the file lists it twice in order
    lines = ['Datum von;Datum bis;Energiemenge in kWh;Ersatzwert']
    wall = [datetime(2022, 10, 30, 1, 45), datetime(2022, 10, 30, 2, 0), datetime(2022, 10, 30, 2, 15),
            datetime(2022, 10, 30, 2, 30), datetime(2022, 10, 30, 2, 45),
            datetime(2022, 10, 30, 2, 0), datetime(2022, 10, 30, 2, 15), datetime(2022, 10, 30, 2, 30),
            datetime(2022, 10, 30, 2, 45), datetime(2022, 10, 30, 3, 0)]
    for w in wall:
        lines.append(f'{w:%d.%m.%Y %H:%M};{w + timedelta(minutes=15):%d.%m.%Y %H:%M};0,1;')
    readings = parse_csv('\n'.join(lines).encode(), TZ)
    utc = [r.start.astimezone(ZoneInfo('UTC')) for r in readings]
    assert all(a < b for a, b in zip(utc, utc[1:])), 'timestamps must be strictly increasing'
    assert all(b - a == timedelta(minutes=15) for a, b in zip(utc, utc[1:]))
    assert [r.start.fold for r in readings] == [0, 0, 0, 0, 0, 1, 1, 1, 1, 0]


def test_parse_csv_rejects_unknown_header():
    with pytest.raises(ParseError):
        parse_csv(b'foo;bar\n1;2\n', TZ)


def test_expected_intervals():
    assert expected_intervals(date(2022, 9, 17), TZ) == 96
    assert expected_intervals(date(2022, 3, 27), TZ) == 92
    assert expected_intervals(date(2022, 10, 30), TZ) == 100


def test_view_state_extraction():
    assert extract_view_state('<input type="hidden" name="jakarta.faces.ViewState" id="x" value="abc=">') == 'abc='
    assert extract_view_state_from_partial(
        '<update id="j_id1:javax.faces.ViewState:0"><![CDATA[vs2]]></update>') == 'vs2'


def test_parse_initial_state_and_unit_field():
    html = (
        '<input name="jakarta.faces.ViewState" value="vs1">'
        '<input type="radio" name="myForm1:grid_eval:selectedClass" id="myForm1:grid_eval:selectedClass:0" value="ConsumDaily">'
        '<input type="radio" name="myForm1:grid_eval:selectedClass" id="myForm1:grid_eval:selectedClass:1" value="ConsumQuarter">'
    )
    state = parse_initial_state(html)
    assert state.granularity_field == 'myForm1:grid_eval:selectedClass'
    assert state.granularity_radio_indices == {'ConsumDaily': '0', 'ConsumQuarter': '1'}
    partial = html + (
        '<input type="radio" name="myForm1:grid_unit:selectedClass" id="myForm1:grid_unit:selectedClass:0" value="KWH">'
        '<input type="radio" name="myForm1:grid_unit:selectedClass" id="myForm1:grid_unit:selectedClass:1" value="EUR">'
    )
    assert find_unit_field(partial, state.granularity_field) == ('myForm1:grid_unit:selectedClass', ('KWH', 'EUR'))


def test_find_csv_button():
    xml = '<a id="myForm1:exportAreaID:j_idt1" href="#"><span>CSV-Datei exportieren</span></a>'
    assert find_csv_button(xml) == 'myForm1:exportAreaID:j_idt1'
    with pytest.raises(ParseError):
        find_csv_button('<a id="x">nope</a>')


def test_chunks_forward_and_backward():
    fwd = list(databot.chunks(date(2024, 1, 1), date(2024, 1, 10), 4))
    assert fwd == [(date(2024, 1, 1), date(2024, 1, 4)), (date(2024, 1, 5), date(2024, 1, 8)),
                   (date(2024, 1, 9), date(2024, 1, 10))]
    back = list(databot.chunks(date(2024, 1, 1), date(2024, 1, 10), 4, backwards=True))
    assert back == [(date(2024, 1, 7), date(2024, 1, 10)), (date(2024, 1, 3), date(2024, 1, 6)),
                    (date(2024, 1, 1), date(2024, 1, 2))]


def test_group_days():
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4)]
    assert databot.group_days(days) == [(date(2024, 1, 1), date(2024, 1, 2)), (date(2024, 1, 4), date(2024, 1, 4))]


def test_cli_defaults():
    args = databot.build_parser().parse_args([])
    assert args.command in (None, 'csv') and args.overlap == databot.DEFAULT_OVERLAP_DAYS
    args = databot.build_parser().parse_args(['csv', '--overlap', '30', '--stop-after-empty', '3'])
    assert args.overlap == 30 and args.stop_after_empty == 3
    assert databot.build_parser().parse_args(['check']).command == 'check'


def test_csv_file_round_trip(tmp_path):
    from linznetz import Reading
    z = ZoneInfo(TZ)
    path = tmp_path / 'readings.csv'
    start = datetime(2024, 1, 1, 0, 0, tzinfo=z)
    readings = [Reading(start=start + timedelta(minutes=15 * i), end=start + timedelta(minutes=15 * (i + 1)),
                        kwh=0.1 * i, substitute=(i == 1)) for i in range(3)]
    rows = databot.readings_to_rows(readings, {readings[0].start.timestamp(): 0.42})
    databot.write_csv_file(path, rows)
    loaded = databot.read_csv_file(path)
    assert list(loaded) == sorted(loaded) and len(loaded) == 3
    assert loaded[readings[0].start.timestamp()] == ['2024-01-01T00:00:00+01:00', '2023-12-31T23:00:00Z', '0.000', '0.420', 'false']
    assert loaded[readings[1].start.timestamp()][2:] == ['0.100', '0.400', 'true']  # kW derived when not supplied
    # merging newer data overwrites the same interval and keeps the rest
    newer = databot.readings_to_rows([Reading(start=readings[2].start, end=readings[2].end, kwh=9.0, substitute=False)], {})
    loaded.update(newer)
    databot.write_csv_file(path, loaded)
    again = databot.read_csv_file(path)
    assert len(again) == 3 and again[readings[2].start.timestamp()][2] == '9.000'


def test_reconstruct_helpers():
    import reconstruct
    m = reconstruct.FILE_RE.match('AT0000000000000000000000000000000_2023-03-06-2023-03-12.png')
    assert m and m.group('start') == '2023-03-06' and m.group('stop') == '2023-03-12'
    assert not reconstruct.FILE_RE.match('current.png')
    assert reconstruct.dst_switch_in_week(date(2023, 3, 24), TZ)      # week of 2023-03-26 switch
    assert not reconstruct.dst_switch_in_week(date(2023, 4, 5), TZ)
    ax = (100, 400, 50, 650)  # top, bottom, left, right
    assert reconstruct.px_to_kwh(ax, 400) == 0 and reconstruct.px_to_kwh(ax, 100) == reconstruct.Y_MAX
    assert abs(reconstruct.data_to_px_x(ax, 3) - 350) < 1e-9  # day 3 is centred


def test_incomplete_days_in_rows():
    from linznetz import Reading
    z = ZoneInfo(TZ)
    rows = {}
    for day, n in ((date(2024, 1, 1), 96), (date(2024, 1, 2), 80), (date(2024, 1, 4), 96)):  # 3rd missing entirely
        start = datetime.combine(day, datetime.min.time(), tzinfo=z)
        for i in range(n):
            t = start + timedelta(minutes=15 * i)
            rows[t.timestamp()] = [t.isoformat(), '', '0.1', '0.4', 'false']
    assert databot.incomplete_days_in_rows(rows, TZ, date(2024, 1, 4)) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert databot.incomplete_days_in_rows({}, TZ, date(2024, 1, 4)) == []


def test_write_unified_trims_boundary(tmp_path):
    z = ZoneInfo(TZ)
    cfg = databot.Config(username='u', password='p', meter_id='M', tz=TZ, export_dir=tmp_path)
    # reconstructed block 18:15-00:15 overlapping the first measured reading at 00:00
    (tmp_path / 'M_reconstructed_6h.csv').write_text(
        'start_local;end_local;kwh;source;method;note\n'
        '2023-08-31T18:15:00+02:00;2023-09-01T00:15:00+02:00;1.13;chart_ocr;label;read from chart\n')
    rows = {}
    for i in range(3):
        t = datetime(2023, 9, 1, 0, 15 * i, tzinfo=z)
        rows[t.timestamp()] = [t.isoformat(), '', '0.027', '0.108', 'true' if i == 1 else 'false']
    out = tmp_path / 'M_all.csv'
    assert databot.write_unified(cfg, rows, out) == 4
    lines = list(__import__('csv').DictReader(out.open(), delimiter=';'))
    assert lines[0]['end_local'] == '2023-09-01T00:00:00+02:00' and lines[0]['kwh'] == '1.103'
    assert 'trimmed' in lines[0]['note'] and lines[0]['source'] == 'chart_ocr'
    assert lines[1]['method'] == 'measured' and lines[2]['method'] == 'substitute'
    assert all(a['end_local'] == b['start_local'] for a, b in zip(lines, lines[1:]))
    assert not any(',' in v for l in lines for v in l.values())
