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
              'statistics': {'consumption': {'id': 'linznetz_energy:place_grid_consumption', 'hours': 3, 'from': '06.03.2023',
                                       'to': '13.10.2026 23:00', 'kwh': 7301.4, 'sources': {'portal': 2},
                                       'flagged': 1}},
              'last_import': {'at': '2026-10-14T15:00:00+00:00', 'lines': ['x.csv: 4 of 12 readings added']}}
    page = W.render_page(status)
    assert 'linznetz_energy:place_grid_consumption' in page and '7301.4 kWh' in page and '1 deviating hours' in page
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
    assert f'Uploaded: {METER}_readings.csv' in page and 'The files are being imported' in page
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


def test_page_explains_the_setup():
    page = W.render_page({'phase': 'setup', 'setup': [['problem.password', {}]]})
    assert 'Setup needed' in page and 'The portal password is missing.' in page and 'Not configured yet' in page
    assert 'http-equiv="refresh"' not in page          # nothing changes until HA restarts the add-on
    assert 'not tried yet' in page and 'pill ok">ok' not in page


def test_page_follows_the_language_of_home_assistant():
    status = {'language': 'de', 'site': 'home', 'metering_point': 'AT' + '0' * 31, 'phase': 'idle',
              'detail': 'detail.new_readings', 'portal': {'ok': '2026-10-14T15:00:00+00:00'},
              'last_import': {'at': '2026-10-14T15:00:00+00:00',
                              'lines': [['import.failed', {'file': 'x.csv', 'reason_key': 'import.other_meter'}]]}}
    page = W.render_page(status)
    assert '<html lang="de">' in page and 'Standort home · Zählpunkt AT0000000' in page   # full metering point
    assert 'Letzter Portal-Abruf' in page and 'Ältere Daten importieren' in page and 'aktuell' in page
    assert 'x.csv: nicht importiert (die Datei gehört zu einem anderen Zählpunkt)' in page
    assert 'Dateien auswählen oder hierher ziehen' in page and 'Hochladen und importieren' in page
    assert '<html lang="en">' in W.render_page({**status, 'language': 'fr'})            # fallback


def test_upload_area_is_prominent():
    page = W.render_page({})
    assert 'class="drop"' in page and 'id="upload" disabled' in page    # enabled once files are chosen
    assert '"{count} file(s) selected"' in page                          # template for the script


def test_page_fits_small_screens():
    page = W.render_page({})
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
    assert 'grid-template-columns: minmax(0, 1fr)' in page        # long values cannot widen the page
    assert 'overflow-wrap: anywhere' in page
    assert '@media (max-width: 600px)' in page                    # stacked label/value on phones


def test_page_scrolls_inside_the_ios_app_frame():
    page = W.render_page({})
    assert 'html { height: 100%; overflow: hidden; }' in page
    assert 'overflow-y: auto; -webkit-overflow-scrolling: touch' in page



def test_energy_card_explains_the_switch_and_double_counting():
    base = {'language': 'de', 'site': 'Lakeside'}
    switch = W.render_page({**base, 'energy': {'grid_from': ['sensor.lakeside_grid_total_energy_consumption'],
                                               'consumption_statistic': 'linznetz_energy:lakeside_grid_consumption',
                                               'uses_consumption': False}})
    assert '<h2>Energie-Dashboard</h2>' in switch and '<ol class="steps">' in switch
    assert '<strong>Einstellungen &gt; Backup</strong>' in switch
    assert ('Bei <strong>Aus dem Netz bezogene Energie</strong> „Lakeside Netzbezug“ statt '
            '<code>sensor.lakeside_grid_total_energy_consumption</code> wählen') in switch
    first = W.render_page({**base, 'energy': {'grid_from': [], 'consumption_statistic': 'x', 'uses_consumption': False}})
    assert '<strong>Netzanschluss hinzufügen</strong>' in first
    double = W.render_page({**base, 'energy': {'grid_from': ['linznetz_energy:lakeside_grid_consumption', 'sensor.x'],
                                               'consumption_statistic': 'linznetz_energy:lakeside_grid_consumption',
                                               'uses_consumption': True}})
    assert 'Nutzt <strong>Lakeside Netzbezug</strong>' in double and '<code>sensor.x</code>' in double
    assert 'doppelt gezählt' in double
    assert '<h2>Energie-Dashboard</h2>' not in W.render_page(base)   # no card before the first run

def test_remove_needs_the_confirmation_box(server):
    url, store, wake = server
    req = urllib.request.Request(url + '/remove', data=b'', method='POST')
    page = urllib.request.urlopen(req).read().decode()
    assert 'Please tick the box to confirm.' in page and not wake.is_set()
    assert 'removal_request' not in W.read_status(store)
    req = urllib.request.Request(url + '/remove', data=b'confirm=yes', method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    page = urllib.request.urlopen(req).read().decode()
    assert 'Removing, the page updates itself.' in page and wake.is_set()
    assert W.read_status(store)['removal_request'] == 'remove'


def test_page_offers_removal_and_after_it_reactivation():
    page = W.render_page({'language': 'de'})
    assert 'Alles aus Home Assistant entfernen' in page and 'name="confirm" value="yes" required' in page
    done = W.render_page({'language': 'de', 'removal': {'state': 'done'}})
    assert 'Aus Home Assistant entfernt' in done and 'action="resume"' in done and 'action="remove"' not in done
    refused = W.render_page({'language': 'de', 'removal': {'state': 'refused'}})
    assert 'nutzt die Statistik noch' in refused
    failed = W.render_page({'language': 'en', 'removal': {'state': 'failed', 'error': 'energy/get_prefs'}})
    assert 'Removing did not work (energy/get_prefs)' in failed


def test_the_confirm_dialog_text_stays_inside_its_attribute():
    page = W.render_page({'language': 'en'})
    assert 'onsubmit="return confirm(&quot;Delete the statistic, the device and the notifications of the add-on?&quot;)"' in page


def test_live_meter_card_lists_the_candidates():
    status = {'language': 'en', 'live_meter': 'sensor.a',
              'live_candidates': [{'entity_id': 'sensor.a', 'name': 'A <grid>', 'state': '12.3', 'unit': 'kWh'},
                                  {'entity_id': 'sensor.b', 'name': 'B', 'state': '4', 'unit': 'Wh'}]}
    page = W.render_page(status)
    assert '<h2>Live meter</h2>' in page and 'action="live_meter"' in page
    assert '<option value="sensor.a" selected>A &lt;grid&gt; (12.3 kWh)</option>' in page
    assert '<option value="">None (portal only)</option>' in page
    assert '<strong>A &lt;grid&gt;</strong><br><code>sensor.a</code> · 12.3 kWh' in page   # in use
    other = W.render_page({**status, 'live_meter': 'sensor.gone'})                 # kept, even if not listed
    assert '<option value="sensor.gone" selected>' in other
    assert '<h2>Live meter</h2>' not in W.render_page({'language': 'en'})        # before the first run
    saved = W.render_page({**status, 'live_meter_result': {'state': 'saved', 'entity': 'sensor.a'}})
    assert 'Saved: sensor.a.' in saved


def test_fetch_button_records_the_request(server):
    url, store, wake = server
    assert 'action="fetch"' in W.render_page({'language': 'en'})
    assert 'action="fetch"' not in W.render_page({'language': 'en', 'phase': 'removed'})
    req = urllib.request.Request(url + '/fetch', data=b'', method='POST')
    page = urllib.request.urlopen(req).read().decode()
    assert 'Fetching from the portal' in page and wake.is_set() and W.read_status(store)['fetch_request'] is True
    assert 'class="secondary small" disabled' in page

def test_live_meter_post_records_the_request(server):
    url, store, wake = server
    req = urllib.request.Request(url + '/live_meter', data=b'entity=sensor.place_grid_total', method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    page = urllib.request.urlopen(req).read().decode()
    assert 'Saving, the page updates itself.' in page and wake.is_set()
    assert W.read_status(store)['live_meter_request'] == {'entity': 'sensor.place_grid_total'}
    W.write_status(store, live_meter_request=None)
    bad = urllib.request.Request(url + '/live_meter', data=b'entity=../x', method='POST')
    page = urllib.request.urlopen(bad).read().decode()
    assert '../x is not a sensor' in page and W.read_status(store)['live_meter_request'] is None


def test_plain_text_password_card_has_the_steps():
    page = W.render_page({'language': 'en', 'password_secret': False})
    assert '<h2>Portal password in plain text</h2>' in page and page.count('<li>') >= 6
    assert '<strong>Edit in YAML</strong>' in page and '<code>secrets.yaml</code>' in page
    assert 'The app works either way.' in page
    assert 'Portal password in plain text' not in W.render_page({'language': 'en', 'password_secret': True})
    assert 'Portal password in plain text' not in W.render_page({'language': 'en'})


def test_status_shows_the_portal_schedule():
    page = W.render_page({'language': 'en', 'portal': {'ok': '2026-10-14T09:07:00+00:00', 'next': '2026-10-14T10:07:00+00:00',
                                                       'next_waits': True, 'usual': '11:07', 'arrivals': 5}})
    assert 'Next portal fetch' in page and 'yesterday is still missing on the portal' in page
    assert 'usually around 11:07 (last 5 days)' in page
    done = W.render_page({'language': 'de', 'portal': {'next': '2026-10-15T04:07:00+00:00', 'next_waits': False}})
    assert 'der gestrige Tag ist vollständig' in done and 'Neue Tage kommen' not in done


def test_actions_redirect_to_the_page_and_show_their_message_once(server):
    url, store, wake = server

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open(urllib.request.Request(url + '/fetch', data=b'', method='POST'))
    assert err.value.code == 303 and err.value.headers['Location'] == './'
    first = urllib.request.urlopen(url + '/').read().decode()
    assert 'Fetching from the portal' in first
    again = urllib.request.urlopen(url + '/').read().decode()            # the page's own refresh
    assert 'Fetching from the portal' not in again
    # an address left over from a form: back to the page instead of "Not Found"
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open(url + '/fetch')
    assert err.value.code == 303
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url + '/nothing')
    assert err.value.code == 404


def test_statistics_card_lists_gaps():
    stat = {'id': 'x', 'hours': 3, 'from': '01.01.2023', 'to': '02.01.2023 00:00', 'kwh': 1.0, 'sources': {}}
    page = W.render_page({'language': 'de', 'statistics': {'consumption': {
        **stat, 'gaps': [{'from': '01.09.2023 00:00', 'to': '01.10.2023 00:00', 'hours': 720}]}}})
    assert '<th>Lücken</th>' in page and '01.09.2023 00:00 bis 01.10.2023 00:00 (720 Stunden)' in page
    assert '„Ältere Daten importieren“' in page
    many = W.render_page({'language': 'en', 'statistics': {'consumption': {
        **stat, 'gaps': [{'from': str(i), 'to': str(i), 'hours': 1} for i in range(7)]}}})
    assert 'and 2 more' in many
    none = W.render_page({'language': 'en', 'statistics': {'consumption': {**stat, 'gaps': []}}})
    assert '<th>Gaps</th><td><span class="muted">none</span>' in none
    assert 'Gaps' not in W.render_page({'language': 'en'})                # nothing written yet


def test_status_writes_from_several_threads_neither_fail_nor_lose_changes(tmp_path):
    """Real case (1.3.1): an upload on the page and the main loop wrote status.json at the same
    moment through the same temporary file; one of them crashed the add-on."""
    errors = []

    def writer(name):
        try:
            for i in range(200):
                W.write_status(tmp_path, **{f'{name}_{i % 5}': i})
        except Exception as err:   # pragma: no cover - the failure this test is about
            errors.append(err)
    threads = [threading.Thread(target=writer, args=(f't{n}',)) for n in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    status = W.read_status(tmp_path)
    assert all(status[f't{n}_{k}'] == 195 + k for n in range(4) for k in range(5))   # no change lost
    assert [p.name for p in tmp_path.iterdir()] == ['status.json']                    # no temporary file left



def test_estimated_hours_are_an_info_not_a_warning():
    stat = {'id': 'x', 'hours': 3, 'from': '01.01.2023', 'to': '02.01.2023 00:00', 'kwh': 1.0, 'sources': {},
            'flagged': 0, 'estimated': 4}
    page = W.render_page({'language': 'de', 'statistics': {'consumption': stat}})
    assert '<span class="pill info">4 geschätzte Stunden</span>' in page and 'Der Portalwert ersetzt sie' in page
    assert 'pill warn' not in page.split('Statistiken')[1].split('</table>')[0]
