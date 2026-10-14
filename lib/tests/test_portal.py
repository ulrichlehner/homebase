from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homebase import portal
from homebase.portal import (
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


# -- An expired session is retried with a fresh login ------------------------------

def test_expired_session_is_retried(monkeypatch):
    client = portal.LinzNetz('u', 'p', retries=2, retry_delay=0)
    calls = []

    def fetch_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise portal.SessionExpiredError('session expired')
        return b'csv'

    monkeypatch.setattr(client, '_fetch_once', fetch_once)
    assert client.fetch_csv(date(2024, 1, 1), date(2024, 1, 2)) == b'csv'
    assert len(calls) == 2


def test_rejected_credentials_are_not_retried(monkeypatch):
    client = portal.LinzNetz('u', 'p', retries=2, retry_delay=0)
    calls = []

    def fetch_once(*args):
        calls.append(args)
        raise portal.LoginError('LINZ NETZ rejected the credentials')

    monkeypatch.setattr(client, '_fetch_once', fetch_once)
    with pytest.raises(portal.LoginError):
        client.fetch_csv(date(2024, 1, 1), date(2024, 1, 2))
    assert len(calls) == 1
