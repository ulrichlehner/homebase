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
    assert 'Letzter Portal-Abruf' in page and 'Ältere Messwerte importieren' in page and 'aktuell' in page
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
    assert 'html { height: 100%; overflow: hidden; overflow: clip; }' in page   # clip: anchor jumps cannot move it either
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
    assert err.value.code == 303 and err.value.headers['Location'] == './#status'    # back to its card
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
    assert '„Ältere Messwerte importieren“' in page
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



def test_live_meter_card_names_the_power_sensor():
    base = {'language': 'en', 'live_meter': 'sensor.a', 'live_candidates': [
        {'entity_id': 'sensor.a', 'name': 'A', 'state': '1', 'unit': 'kWh'}]}
    page = W.render_page({**base, 'live_power': 'sensor.a_power'})
    assert '<th>Power</th><td><code>sensor.a_power</code>' in page
    assert 'no power sensor on the same device' in W.render_page({**base, 'live_power': None})
    assert '<th>Power</th>' not in W.render_page(base)                          # before the first live update



def test_costs_card_shows_contract_price_years_and_gaps():
    status = {'language': 'de', 'statistics': {}, 'costs': {
        'id': 'linznetz_energy:x_grid_cost', 'from': '06.03.2023', 'eur': 1234.5, 'years': {'2025': 612.4, '2026': 480.0},
        'last12': 650.1, 'unpriced': ['2026-11'], 'energy_ct': 20.82, 'kwh_ct_gross': 32.75, 'grid': 'LINZ NETZ',
        'fetch_error': 'not published yet (latest: 10/2026)',
        'contract': {'supplier': 'go green energy (strom flex)', 'supplier_id': 'gogreen_flex',
                     'product': 'strom flex future', 'start': '01.03.2023', 'start_iso': '2023-03-01', 'params': {}}}}
    page = W.render_page(status)
    assert '<h2>Kosten</h2>' in page and 'strom flex future seit 01.03.2023' in page
    assert '32,75 ct/kWh inkl. Netz, Abgaben und USt. (Energiepreis 20,82 ct/kWh netto)' in page
    assert 'noch nicht veröffentlicht' in page and 'pill warn' not in page.split('<h2>Kosten</h2>')[1].split('</table>')[0]
    assert '2025: 612,40 €' in page and 'letzte 12 Monate: 650,10 €' in page and '11/2026' in page
    assert '<option value="strom flex future" selected>' in page and 'action="contract"' in page
    assert 'name="start" required value="2023-03-01"' in page                  # the current contract's start
    ok = W.render_page({**status, 'costs': {**status['costs'], 'fetch_error': None}})   # the usual case
    card = ok.split('id="costs"')[1].split('</table>')[0]
    assert 'pill info">i' not in card and 'failed' not in ok
    empty = W.render_page({'language': 'en', 'statistics': {}, 'costs': {'contract': None, 'grid': 'LINZ NETZ'}})
    assert 'not set yet: see <strong>Setup</strong>' in empty



COMPARISON = {'product': 'strom flex future', 'through': '2026-09', 'offer_error': None,
              'rows': [{'month': '2026-08', 'ct': 13.44, 'fee': 3.0, 'until': '2027-12-31', 'eur': -14.78, 'ct_kwh': -5.12,
                        'kwh': 288.0, 'assumed': False},
                       {'month': '2026-09', 'ct': 13.44, 'fee': 3.0, 'until': None, 'eur': 3.2, 'ct_kwh': 1.1,
                        'kwh': 290.0, 'assumed': True}],
              'months': [{'month': '2026-08', 'actual_ct': 16.0, 'fixed_ct': 13.44},
                         {'month': '2026-09', 'actual_ct': 20.7, 'fixed_ct': 13.44}],
              'today': {'seen': '2026-10-05', 'ct': 20.28, 'fee': 3.0, 'until': '2028-12-31', 'months': 2, 'kwh': 578.0,
                        'actual_ct': 19.9, 'fixed_ct': 21.3, 'eur': 8.1, 'eur_per_ct_year': 34.68}}


def test_comparison_card_shows_today_the_chart_and_the_start_months():
    page = W.render_page({'language': 'de', 'comparison': COMPARISON})
    card = page.split('id="comparison"')[1]
    assert 'Vergleich mit Fixpreis' in card and 'strom future: 20,28 ct/kWh + 3,00 € pro Monat, Preisgarantie bis 31.12.2028' in card
    assert 'Der Fixpreis hätte 8,10 € mehr gekostet.' in card and '34,68 €' in card
    assert '<svg viewBox' in card and card.count('<polyline') == 2
    rows = card.split('<table class="grid">')[1].split('</table>')[0]
    assert rows.index('09/2026') < rows.index('08/2026')                       # newest first
    assert 'class="less">−14,78 €' in rows and 'class="more">+3,20 €' in rows and '?*' in rows
    assert 'Die Preisgarantie endete' in card
    assert 'id="comparison"' not in W.render_page({'language': 'de', 'comparison': None})


def test_costs_card_says_where_the_grid_charges_come_from():
    base = {'language': 'en', 'statistics': {}, 'costs': {'contract': None, 'grid': 'LINZ NETZ'}}
    def grid_row(g):
        page = W.render_page({**base, 'costs': {**base['costs'], 'grid_status': g}})
        return page.split('Grid operator')[1].split('</tr>')[0]
    assert 'checked today' in grid_row({'state': 'online'})
    assert 'pill info' in grid_row({'state': 'table', 'error': 'offline'}) and 'offline' in grid_row({'state': 'table', 'error': 'offline'})
    outdated = grid_row({'state': 'outdated', 'year': 2027})
    assert 'pill warn' in outdated and 'for 2027 are missing' in outdated
    assert 'pill' not in grid_row({})

def test_contract_post_records_the_request_and_returns_to_the_card(server):
    url, store, wake = server

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    req = urllib.request.Request(url + '/contract', method='POST',
                                 data=b'supplier=fixed&start=2026-11-01&energy_ct_kwh=15%2C9&base_fee_month=3')
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.build_opener(NoRedirect).open(req)
    assert err.value.code == 303 and err.value.headers['Location'] == './#contract' and wake.is_set()
    status = W.read_status(store)
    assert status['contract_request'] == {'supplier': 'fixed', 'start': '2026-11-01', 'energy_ct_kwh': '15,9',
                                          'base_fee_month': '3'}
    assert status['contract_result'] == {'state': 'pending'} and 'notice' not in status      # shown in the card
    page = W.render_page({**status, 'statistics': {}})
    assert 'Saved. The costs are being calculated' in page and 'http-equiv="refresh"' in page

def test_energy_card_includes_the_cost_step_once_costs_exist():
    energy = {'grid_from': ['sensor.x'], 'consumption_statistic': 'linznetz_energy:lakeside_grid_consumption',
              'uses_consumption': False}
    base = {'language': 'de', 'site': 'Lakeside', 'energy': energy}
    assert '<li>Speichern.</li>' in W.render_page(base)
    page = W.render_page({**base, 'costs': {'from': '06.03.2023', 'contract': None}})
    assert '<strong>Kostenerfassung</strong>' in page and '„Lakeside Stromkosten“' in page



CONTRACT = {'supplier': 'go green energy (strom flex)', 'supplier_id': 'gogreen_flex', 'product': 'strom flex future',
            'start': '01.03.2023', 'start_iso': '2023-03-01', 'params': {}}
DONE = {'language': 'de', 'site': 'Lakeside', 'portal': {'ok': '2026-10-14T12:00:00+00:00'},
        'data': {'readings': {'count': 105120, 'from': '01.10.2023', 'to': '13.10.2026'}, 'reconstructed': None, 'live': None},
        'statistics': {'consumption': {'id': 'x', 'hours': 1, 'from': 'a', 'to': 'b', 'kwh': 1.0, 'gaps': []}},
        'live_meter': 'sensor.lakeside_grid_energy',
        'costs': {'id': 'x', 'from': '06.03.2023', 'eur': 1.0, 'years': {}, 'last12': 0, 'unpriced': [],
                  'contract': CONTRACT, 'grid': 'LINZ NETZ'},
        'energy': {'grid_from': ['linznetz_energy:lakeside_grid_consumption'], 'uses_consumption': True, 'uses_cost': True,
                   'consumption_statistic': 'linznetz_energy:lakeside_grid_consumption'}}


def states(status):
    return {step: state for step, state, _ in W.setup_steps(status)}


def section(page, name):
    return page.split(f'<section class="tab" id="{name}"')[1].split('</section>')[0]


def test_the_page_has_three_tabs_with_the_cards_in_their_place():
    page = W.render_page({**DONE, 'comparison': None})
    assert page.count('<section class="tab"') == 3 and 'data-tab="overview"' in page and 'data-tab="data"' in page
    overview, setup, data = (section(page, n) for n in ('overview', 'setup', 'data'))
    assert 'id="costs"' in overview and 'id="summary"' in overview and 'action="contract"' not in overview
    assert 'id="checklist"' in setup and 'id="supplier"' in setup and 'action="contract"' in setup and 'id="energy"' in setup
    assert 'id="status"' in data and 'id="history"' in data and 'id="remove"' in data
    assert '"overview"' in page.split('var tabs = ')[1].split('key =')[0]           # all done: opens on the overview


def test_the_checklist_follows_the_setup():
    fresh = {'language': 'de', 'setup': [['problem.username', {}]], 'statistics': {}}
    assert states(fresh) == {'portal': 'todo', 'download': 'later', 'history': 'later', 'live': 'optional',
                             'supplier': 'todo', 'prices': 'later', 'dashboard': 'later'}
    half = {**DONE, 'live_meter': '', 'costs': {**DONE['costs'], 'contract': None, 'from': None},
            'energy': {'grid_from': ['sensor.x'], 'uses_consumption': False}}
    assert states(half) == {'portal': 'done', 'download': 'done', 'history': 'done', 'live': 'optional',
                            'supplier': 'todo', 'prices': 'later', 'dashboard': 'todo'}
    assert W.open_required(W.setup_steps(half)) == 2
    assert set(states(DONE).values()) == {'done'}
    unpriced = {**DONE, 'costs': {**DONE['costs'], 'unpriced': ['2023-03']},
                'energy': {**DONE['energy'], 'uses_cost': False}}
    assert states(unpriced)['prices'] == 'optional' and states(unpriced)['dashboard'] == 'todo'   # costs not used yet
    page = W.render_page(half)
    checklist = section(page, 'setup').split('id="checklist"')[1].split('</ol>')[0]
    assert 'Erste Schritte' in checklist and 'noch 2 nötige(r) Schritt(e) offen' in page
    assert '<a href="#supplier">Dorthin</a>' in checklist and '(optional)' in checklist
    assert '105\u00a0120 Viertelstunden seit 01.10.2023' in checklist


def test_the_guide_opens_the_setup_until_it_is_skipped():
    half = {**DONE, 'costs': {**DONE['costs'], 'contract': None}}
    guided = W.render_page(half)
    assert '"setup"' in guided.split('var tabs = ')[1].split('key =')[0] and 'Vorerst überspringen' in guided
    assert 'pill run count">1</span>' in guided                                    # the tab says how many are open
    skipped = W.render_page({**half, 'guide_skipped': True})
    assert '"overview"' in skipped.split('var tabs = ')[1].split('key =')[0] and 'Vorerst überspringen' not in skipped
    assert 'Einrichtung: noch 1 Schritt(e) offen.' in section(skipped, 'overview')  # still findable
    assert 'Einrichtung im Überblick' in skipped
    done = W.render_page(DONE)
    assert 'Alles Nötige ist eingerichtet' in done and 'Einrichtung: noch' not in done


def test_the_overview_lists_what_needs_a_look():
    page = W.render_page({**DONE, 'portal': {'ok': None, 'login_failed': '2026-10-14T12:00:00+00:00'},
                          'statistics': {'consumption': {**DONE['statistics']['consumption'],
                                                         'gaps': [{'from': 'a', 'to': 'b', 'hours': 3}]}},
                          'costs': {**DONE['costs'], 'unpriced': ['2023-03', '2023-04']}})
    card = section(page, 'overview').split('id="attention"')[1].split('</ul>')[0]
    assert 'Das Portal lehnt die Anmeldung ab.' in card and '1 Lücke(n) in der Verbrauchsstatistik.' in card
    assert '2 Monat(e) ohne Energiepreis.' in card and '<a href="#price-upload">' in card
    assert 'id="attention"' not in W.render_page(DONE)                              # nothing to look at: no card


def test_skipping_the_guide_is_kept(server):
    url, store, wake = server

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url + '/onboarding', data=b'guide=skip', method='POST'))
    assert err.value.headers['Location'] == './#overview' and W.read_status(store)['guide_skipped'] is True
    assert not wake.is_set()                                                        # nothing for the main loop


def test_a_price_list_uploaded_in_the_card_returns_there(server):
    url, store, wake = server

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    ctype, body = multipart([('prices.csv', b'valid_from;valid_to;kind;component;unit;value;vat\n')])
    req = urllib.request.Request(url + '/upload?to=costs', data=body, headers={'Content-Type': ctype}, method='POST')
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.build_opener(NoRedirect).open(req)
    assert err.value.headers['Location'] == './#price-upload' and (store / 'import' / 'prices.csv').exists()
    assert W.read_status(store)['prices_import'] == {'state': 'importing'} and 'notice' not in W.read_status(store)



def test_months_are_shown_as_ranges():
    assert W._month_ranges(['2023-03', '2023-04', '2023-05', '2023-12', '2024-01', '2025-06']) == \
        '03/2023 – 05/2023, 12/2023 – 01/2024, 06/2025'



def test_card_feedback_sits_where_the_action_was():
    contract = {'supplier': 'go green energy (strom flex)', 'supplier_id': 'gogreen_flex', 'product': 'strom flex future',
                'start': '01.03.2023', 'start_iso': '2023-03-01', 'params': {}}
    page = W.render_page({'language': 'en', 'statistics': {}, 'contract_result': {'state': 'saved'},
                          'prices_import': {'state': 'done', 'lines': [['import.prices', {'file': 'prices.csv', 'rows': 40, 'rejected': 0}]]},
                          'costs': {'contract': contract, 'grid': 'LINZ NETZ', 'unpriced': []}})
    upload = page.split('id="price-upload"')[1].split('</form>')[0]
    assert 'prices.csv: 40 prices taken over' in upload
    form = page.split('id="contract"')[1].split('</form>')[0]
    assert 'Saved, the costs are calculated.' in form
