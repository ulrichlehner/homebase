import threading
import urllib.error
import urllib.request

import pytest

import web as W

METER = 'AT0000000000000000000000000000000'


def multipart(files: list, boundary: str = 'XyZ') -> tuple:
    body = b''
    for name, data in files:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                 f'Content-Type: text/csv\r\n\r\n').encode() + data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    return f'multipart/form-data; boundary={boundary}', body


def test_parse_upload_returns_files_and_strips_paths():
    ctype, body = multipart([('a.csv', b'x;y\n1;2\n'), ('../../evil/b.csv', b'z\n')])
    assert W.parse_upload(ctype, body) == [('a.csv', b'x;y\n1;2\n'), ('b.csv', b'z\n')]
    assert W.parse_upload('text/plain', b'nothing') == []


def test_store_upload_is_atomic_and_safe(tmp_path):
    target = W.store_upload(tmp_path / 'import', 'we ird;name.csv', b'data')
    assert target.name == 'weirdname.csv' and target.read_bytes() == b'data'
    assert [p.name for p in (tmp_path / 'import').iterdir()] == ['weirdname.csv']   # no .part left


def test_status_is_merged(tmp_path):
    W.write_status(tmp_path, phase='idle', site='place')
    W.write_status(tmp_path, phase='downloading')
    assert W.read_status(tmp_path) == {'phase': 'downloading', 'site': 'place'}


def test_page_renders_status_and_refreshes_only_while_busy():
    status = {'site': 'place', 'meter_suffix': '5000', 'phase': 'idle',
              'data': {'readings': {'count': 12, 'from': '01.09.2023 00:00', 'to': '14.10.2026 00:45'}},
              'statistics': {'fused': {'id': 'linznetz:place_grid_energy_fused', 'hours': 3, 'from': '06.03.2023',
                                       'to': '13.10.2026 23:00', 'kwh': 7301.4, 'sources': {'portal': 2},
                                       'flagged': 1}},
              'last_import': {'at': '2026-10-14T15:00:00+00:00', 'lines': ['x.csv: 4 of 12 readings added']}}
    page = W.render_page(status)
    assert 'linznetz:place_grid_energy_fused' in page and '7301.4 kWh' in page and '1 deviating hours' in page
    assert 'x.csv: 4 of 12 readings added' in page and 'http-equiv="refresh"' not in page
    assert 'http-equiv="refresh"' in W.render_page({**status, 'phase': 'downloading'})
    assert '&lt;script&gt;' in W.render_page({'site': '<script>'})   # escaped


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv('ADDON_WEB_ALLOW_ANY', '1')
    wake = threading.Event()
    srv = W.start(tmp_path, wake, port=0)
    yield f'http://127.0.0.1:{srv.server_address[1]}', tmp_path, wake
    srv.shutdown()


def test_upload_stores_files_and_wakes_the_loop(server):
    url, store, wake = server
    ctype, body = multipart([(f'{METER}_readings.csv', b'start_local;start_utc;kwh;kw;substitute\n')])
    req = urllib.request.Request(url + '/upload', data=body, headers={'Content-Type': ctype}, method='POST')
    page = urllib.request.urlopen(req).read().decode()
    assert f'Uploaded {METER}_readings.csv.' in page and 'import pending' in page
    assert (store / 'import' / f'{METER}_readings.csv').exists()
    assert wake.is_set() and W.read_status(store)['import_pending'] is True
    assert urllib.request.urlopen(url + '/upload').status == 200    # the page refreshes there
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url + '/other')
    assert err.value.code == 404


def test_only_the_ingress_proxy_may_connect(tmp_path, monkeypatch):
    monkeypatch.delenv('ADDON_WEB_ALLOW_ANY', raising=False)
    srv = W.start(tmp_path, threading.Event(), port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/')
        assert err.value.code == 403
    finally:
        srv.shutdown()
