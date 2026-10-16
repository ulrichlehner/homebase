"""Texts the user sees (notifications and the add-on page), in the language of Home Assistant.

German and English; any other language falls back to English. The log stays English.
`t(lang, key, **values)` returns the text with the values filled in.
"""

from __future__ import annotations

TEXTS = {
    'en': {
        # setup problems
        'problem.username': 'The portal username is missing. It is your login for the LINZ NETZ service portal.',
        'problem.password': 'The portal password is missing.',
        'problem.metering_point': 'The metering point is missing. It is the Zählpunktnummer on your bill or in the '
                                  'portal: 33 characters, starting with AT.',
        'problem.metering_point_length': 'The metering point has {count} characters, but it needs 33, starting with AT. '
                                         'Please compare it with your bill (the Zählpunktnummer, not the meter number).',
        'problem.metering_point_prefix': 'The metering point has to start with AT. Please compare it with your bill '
                                         '(the Zählpunktnummer, not the meter number).',
        'problem.site': 'The site needs at least one letter or digit.',
        # notifications
        'secret.title': 'LINZ NETZ Energy: portal password in plain text',
        'secret.intro': 'The portal password is stored as plain text in the app configuration, and its YAML view shows '
                        'it. Home Assistant keeps passwords in the file `secrets.yaml`; the configuration then only '
                        'names them. To move it there:',
        'secret.step1': '**Settings > Apps > Install app**: search "File editor", **Install**, then **Start** and switch '
                        'on **Show in sidebar**.',
        'secret.step2': 'Open **File editor** in the sidebar, click the folder icon at the top left and open `secrets.yaml`.',
        'secret.step3': 'Add a line at the end, with the quotes: `linznetz_password: "your portal password"`. Save '
                        'with the disk icon at the top right: it is red while something is unsaved. Then wait a '
                        'minute: Home Assistant reads secrets.yaml again at most once a minute.',
        'secret.step4': '**Settings > Apps > LINZ NETZ Energy**, tab **Configuration**: menu (⋮) at the top right of the '
                        'options > **Edit in YAML**.',
        'secret.step5': 'Replace the line `portal_password: …` with `portal_password: "!secret linznetz_password"` and '
                        'click **Save**. If it reports "Unknown secret": check that secrets.yaml is saved, wait a minute, '
                        'reload the page and try again.',
        'secret.step6': 'Tab **Info**: **Restart**. This message goes away; File editor can be uninstalled again.',
        'secret.outro': 'The app works either way.',
        'page.secret_title': 'Portal password in plain text',
        'setup.title': 'LINZ NETZ Energy: setup needed',
        'setup.intro': 'The app needs a few settings before it can fetch anything:',
        'setup.howto': 'Open the app "LINZ NETZ Energy" in Home Assistant, fill in the fields on the Configuration tab '
                       'and save. Then restart the app on the Info tab.',
        'download.title': 'LINZ NETZ Energy: first download',
        'download.text': 'Downloading everything the LINZ NETZ portal still has (about three years). This takes about '
                         'a minute; the app page shows the progress.',
        'download_done.title': 'LINZ NETZ Energy: first download done',
        'download_done.text': '{count} quarter-hour readings from {first} to {last}. Older history can be uploaded on '
                              'the app page (Open Web UI, "Import older history").',
        'no_data.title': 'LINZ NETZ Energy: no data',
        'no_data.text': 'The portal has no readings for this metering point. Is the quarter-hour analysis '
                        '("Viertelstundenauswertung") switched on in the portal?',
        'download_failed.title': 'LINZ NETZ Energy: first download failed',
        'download_failed.text': 'The portal did not answer as expected ({error}). The app tries again in an hour.',
        'login_failed.title': 'LINZ NETZ Energy: portal login failed',
        'login_failed.text': 'The LINZ NETZ portal rejected the login. Please check the portal username and password '
                             'on the Configuration tab of the app. Next attempt in {hours} hours.',
        'import.title': 'LINZ NETZ Energy: history imported',
        'import.readings': '{file}: {added} of {total} readings added',
        'import.reconstructed': '{file}: {rows} reconstructed quarter hours taken over',
        'import.failed': '{file}: not imported ({reason})',
        'import.other_meter': 'it belongs to another metering point',
        'import.bad_header': 'the file does not have the expected columns',
        'import.unknown_file': 'only <metering point>_readings.csv and <metering point>_reconstructed_15min.csv can be '
                               'imported',
        # page
        'stat.consumption': '{site} grid consumption',
        'stat.cost': '{site} electricity cost',
        'import.prices': '{file}: {rows} prices taken over ({rejected} rows not readable)',
        'page.costs': 'Costs',
        'page.costs_lead': 'What the consumption costs, as on the bills of grid operator and supplier together: '
                           'energy price and fees of the supplier, grid charges and levies, VAT.',
        'page.costs_grid': 'Grid operator',
        'page.costs_grid_unknown': 'no prices for this grid operator yet: only the supplier is counted',
        'page.costs_supplier': 'Supplier',
        'page.costs_no_contract': 'not set yet: choose it below',
        'page.costs_since': '{product} since {start}',
        'page.costs_price': 'Price now',
        'page.costs_price_line': '{gross} ct/kWh incl. grid, levies and VAT (energy price {energy} ct/kWh net), plus fixed fees',
        'page.costs_price_none': 'no energy price for this month yet',
        'page.costs_fetch_error': 'fetching the price failed: {error}; the add-on tries again',
        'page.costs_price_pending': 'the supplier has not published this month\'s price yet; the add-on checks every 6 hours',
        'page.costs_years': 'Per year',
        'page.costs_last12': 'last 12 months: {eur} €',
        'page.costs_unpriced': 'Months without energy price',
        'page.costs_unpriced_hint': 'These months count only grid charges, levies and fees. A price list fills them '
                                    '(see the documentation, "Costs").',
        'page.costs_statistic': 'Statistic',
        'page.costs_statistic_line': '{eur} € since {first}',
        'page.contract': 'Set the supplier',
        'page.contract_supplier': 'Supplier',
        'page.contract_product': 'Product',
        'page.contract_start': 'Valid from',
        'page.contract_base_fee': 'Basic fee (EUR per month, net)',
        'page.contract_online_bonus': 'Online bonus (EUR per month, net, negative)',
        'page.contract_energy': 'Energy price (ct/kWh, net)',
        'page.contract_hint': 'A new supplier from a date ends the previous one; the costs before stay as they were.',
        'page.contract_save': 'Save',
        'page.contract_requested': 'Saving, the page updates itself.',
        'page.contract_saved': 'Saved. The costs are recalculated.',
        'page.contract_invalid': 'Not saved: please check the date and the numbers.',
        'deviation.title': 'LINZ NETZ Energy: live meter and portal differ',
        'deviation.text': 'On these days the live meter readings differ from the portal by more than 2 % '
                          '(live / portal): {details}. The live values are kept; please check the live source (for example the receiver) and the meter.',
        'entity.last_portal_fetch': 'Last portal fetch',
        'entity.portal_data_until': 'Portal data until',
        'entity.live_data_until': 'Live data until',
        'entity.live_deviation': 'Live deviation last day',
        'entity.deviating_hours': 'Deviating hours last 7 days',
        'entity.portal_login_problem': 'Portal login problem',
        'phase.live': 'reading the live meter',
        'page.live': 'Live meter readings',
        'page.live_hours': '{count} hours, {first} to {last}',
        'page.live_check': 'Live vs portal',
        'page.live_check_line': 'last complete day {day}: {percent} %; {hours} deviating hours in 7 days',
        'page.live_check_none': 'no day with both complete yet',
        'page.live_meter': 'Live meter',
        'page.live_meter_lead': 'The meter\'s live counter in Home Assistant becomes the primary source of the consumption '
                                'statistic; the portal checks it and fills gaps.',
        'page.live_meter_none': 'None (portal only)',
        'page.live_meter_save': 'Save',
        'page.live_meter_no_candidates': 'Home Assistant has no sensor that counts energy upwards (kWh) yet.',
        'page.live_meter_requested': 'Saving, the page updates itself.',
        'page.live_meter_saved': 'Saved: {entity}. It is used right away.',
        'page.live_meter_unknown': '{entity} is not a sensor that counts energy upwards.',
        'page.energy': 'Energy dashboard',
        'page.energy_ok': 'Uses **{name}** as energy imported from the grid.',
        'page.energy_double': 'It also uses {others}: remove it there, otherwise the consumption is counted twice.',
        'page.energy_switch': '**{name}** holds the whole history, the live meter readings and the gaps filled by the '
                              'portal. To use it in the Energy dashboard:',
        'page.fetch_now': 'Fetch now',
        'page.fetch_requested': 'Fetching from the portal, the page updates itself.',
        'page.live_meter_in_use': 'In use',
        'page.live_power': 'Power',
        'page.live_power_hint': 'from the same device; it splits the counter when the meter sends no usable reading for a while',
        'page.live_power_none': 'no power sensor on the same device: during reception gaps the consumption is split evenly '
                                '(an estimate until the portal has the hours)',
        'page.live_meter_choose': 'Choose a counter',
        'page.energy_uses': 'Grid import now',
        'page.energy_step_backup': '**Settings > Backup**: create a backup.',
        'page.energy_step_open': '**Settings > Energy**, section **Electricity grid**: open **Edit grid connection** '
                                 '(pencil) at the grid connection.',
        'page.energy_step_pick': 'Under **Energy imported from grid**, choose "{name}" instead of {current}.',
        'page.energy_step_add': 'Choose **Add grid connection** and pick "{name}" under **Energy imported from grid**.',
        'page.energy_step_cost': 'Under **Cost tracking**, choose **Use an entity tracking the total costs** and pick '
                                 '"{cost}", then save.',
        'page.energy_step_save': 'Save.',
        'page.energy_undo': 'This can be undone at any time the same way; the statistics of the old sensor stay.',
        'page.energy_none': 'nothing yet',
        'page.energy_unknown': 'The configuration of the Energy dashboard could not be read.',
        'page.remove_title': 'Remove everything from Home Assistant',
        'page.remove_text': 'Before uninstalling the add-on: this deletes what it created in Home Assistant (the '
                            'statistic, the device with its status entities, its notifications) and pauses the add-on. '
                            'Its own files stay until you uninstall it with "delete data". Not possible while the '
                            'Energy dashboard uses the statistic.',
        'page.remove_check': 'I understand that the statistic with the whole history is deleted from Home Assistant.',
        'page.remove_confirm': 'Delete the statistic, the device and the notifications of the add-on?',
        'page.remove_button': 'Remove everything',
        'page.remove_unconfirmed': 'Please tick the box to confirm.',
        'page.remove_requested': 'Removing, the page updates itself.',
        'page.remove_refused': 'The Energy dashboard still uses the statistic. Remove it there first '
                               '(Settings > Dashboards > Energy), then try again.',
        'page.remove_failed': 'Removing did not work ({error}), nothing is paused. Details are in the log; '
                              'try again later.',
        'page.removed_title': 'Removed from Home Assistant',
        'page.removed_text': 'The statistic, the device and the notifications are gone, and the add-on is paused. Now '
                             'uninstall it and tick "delete data" to remove its files too. Changed your mind? '
                             'Reactivate it: everything is created again within an hour.',
        'page.resume': 'Reactivate',
        'page.resume_requested': 'Reactivating, the page updates itself.',
        'phase.removing': 'removing',
        'phase.removed': 'removed, paused',
        'page.not_configured': 'Not configured yet',
        'page.site': 'Site {site} · metering point {metering_point}',
        'source.live': 'live meter',
        'source.portal': 'portal',
        'source.portal_substitute': 'portal substitute values',
        'source.reconstructed': 'reconstructed',
        'source.portal_partial': 'portal, incomplete hours',
        'page.setup_title': 'Setup needed',
        'page.status': 'Status',
        'page.state': 'State',
        'page.last_run': 'Last run',
        'page.next_run': 'Next run',
        'page.portal_login': 'Portal login',
        'page.last_fetch': 'Last portal fetch',
        'page.portal_next': 'Next portal fetch',
        'page.portal_next_text': '{time} (yesterday is still missing on the portal; it is looked for every hour between 06:00 and 22:00)',
        'page.portal_next_done': '{time} (yesterday is complete; the next day is looked for from 06:00)',
        'page.portal_usual': 'New days arrive',
        'page.portal_usual_text': 'usually around {time} (last {count} days)',
        'page.never': 'never',
        'page.not_tried': 'not tried yet',
        'page.login_ok': 'ok',
        'page.login_rejected': 'rejected',
        'page.data': 'Data',
        'page.readings': 'Portal readings',
        'page.reconstructed': 'Reconstructed history',
        'page.none': 'none',
        'page.quarter_hours': '{count} quarter hours, {first} to {last}',
        'page.statistics': 'Statistics',
        'page.stat_consumption': 'Grid consumption (for the Energy dashboard)',
        'page.not_written': 'not written yet',
        'page.stat_line': '{hours} hours, {first} to {last}, {kwh} kWh',
        'page.deviating': '{count} deviating hours',
        'page.estimated': '{count} estimated hours',
        'page.estimated_hint': 'The live meter had a reception gap at the hour boundary (the meter sends no usable '
                               'counter for about half an hour now and then), so the split between the two hours is an '
                               'estimate; their sum is exact. The portal value replaces them a day or two later.',
        'page.gaps': 'Gaps',
        'page.gaps_none': 'none',
        'page.gap_line': '{first} to {last} ({hours} hours)',
        'page.gaps_more': 'and {count} more',
        'page.gaps_hint': 'No source covers these hours. Older readings (for example the readings.csv of the '
                          'homebase tools) fill them via "Import older history" below.',
        'page.import_title': 'Import older history',
        'page.import_lead': 'The portal keeps only about 36 months. Older data from the homebase tools can be added '
                            'here; the files are checked and imported right away, and importing the same file again '
                            'changes nothing.',
        'page.metering_point': 'metering point',
        'page.file_readings': 'older portal readings, merged row by row',
        'page.file_reconstructed': 'the reconstructed history (6-hour values split into quarter hours)',
        'page.drop': 'Choose files or drop them here',
        'page.drop_hint': 'CSV files, both at once is fine',
        'page.selected': '{count} file(s) selected',
        'page.upload': 'Upload and import',
        'page.import_pending': 'The files are being imported, the page updates itself.',
        'page.last_import': 'Last import',
        'page.nothing_imported': 'Nothing imported yet.',
        'page.uploaded': 'Uploaded: {files}',
        'page.too_large': 'The upload is too large.',
        'page.no_file': 'No file was uploaded.',
        'phase.idle': 'up to date',
        'phase.starting': 'starting',
        'phase.importing': 'importing',
        'phase.downloading': 'downloading from the portal',
        'phase.statistics': 'writing statistics',
        'phase.setup': 'setup needed',
        'phase.error': 'error',
        'detail.first_download': 'first download, about a minute',
        'detail.new_readings': 'new readings',
        'detail.older_history': 'older history',
    },
    'de': {
        'problem.username': 'Der Portal-Benutzername fehlt. Das ist dein Login für das LINZ NETZ Serviceportal.',
        'problem.password': 'Das Portal-Passwort fehlt.',
        'problem.metering_point': 'Der Zählpunkt fehlt. Das ist die Zählpunktnummer auf der Rechnung oder im Portal: '
                                  '33 Zeichen, beginnt mit AT.',
        'problem.metering_point_length': 'Der Zählpunkt hat {count} Zeichen, es müssen aber 33 sein, beginnend mit AT. '
                                         'Bitte mit der Rechnung vergleichen (die Zählpunktnummer, nicht die '
                                         'Zählernummer).',
        'problem.metering_point_prefix': 'Der Zählpunkt muss mit AT beginnen. Bitte mit der Rechnung vergleichen '
                                         '(die Zählpunktnummer, nicht die Zählernummer).',
        'problem.site': 'Der Standort braucht mindestens einen Buchstaben oder eine Ziffer.',
        'secret.title': 'LINZ NETZ Energy: Portal-Passwort im Klartext',
        'secret.intro': 'Das Portal-Passwort steht im Klartext in der Konfiguration der App, ihre YAML-Ansicht zeigt es '
                        'an. Home Assistant hält Passwörter in der Datei `secrets.yaml`; die Konfiguration nennt sie dann '
                        'nur beim Namen. So zieht es dorthin um:',
        'secret.step1': '**Einstellungen > Apps > App installieren**: nach „File editor“ suchen, **Installieren**, dann '
                        '**Starten** und **In Seitenleiste anzeigen** einschalten.',
        'secret.step2': '**File editor** in der Seitenleiste öffnen, oben links auf das Ordnersymbol klicken und '
                        '`secrets.yaml` öffnen.',
        'secret.step3': 'Am Ende eine Zeile ergänzen, mit den Anführungszeichen: `linznetz_password: "dein Portal-Passwort"`. '
                        'Mit dem Diskettensymbol oben rechts speichern: es ist rot, solange etwas ungespeichert ist. '
                        'Dann eine Minute warten: Home Assistant liest secrets.yaml höchstens einmal pro Minute neu.',
        'secret.step4': '**Einstellungen > Apps > LINZ NETZ Energy**, Tab **Konfiguration**: Menü (⋮) oben rechts bei den '
                        'Optionen > **In YAML bearbeiten**.',
        'secret.step5': 'Die Zeile `portal_password: …` ersetzen durch `portal_password: "!secret linznetz_password"` und '
                        '**Speichern** klicken. Meldet es „Unknown secret“: prüfen, ob secrets.yaml gespeichert ist, eine '
                        'Minute warten, die Seite neu laden und nochmals versuchen.',
        'secret.step6': 'Tab **Info**: **Neu starten**. Diese Meldung verschwindet; File editor kann danach wieder '
                        'deinstalliert werden.',
        'secret.outro': 'Die App funktioniert so oder so.',
        'page.secret_title': 'Portal-Passwort im Klartext',
        'setup.title': 'LINZ NETZ Energy: Einrichtung nötig',
        'setup.intro': 'Die App braucht noch ein paar Einstellungen, bevor sie Daten holen kann:',
        'setup.howto': 'Öffne in Home Assistant die App „LINZ NETZ Energy“, fülle im Tab Konfiguration die Felder aus '
                       'und speichere. Starte die App danach im Tab Info neu.',
        'download.title': 'LINZ NETZ Energy: erster Download',
        'download.text': 'Alles, was das LINZ NETZ Portal noch hat (etwa drei Jahre), wird geladen. Das dauert etwa '
                         'eine Minute; die Seite der App zeigt den Fortschritt.',
        'download_done.title': 'LINZ NETZ Energy: erster Download fertig',
        'download_done.text': '{count} Viertelstundenwerte vom {first} bis {last}. Ältere Daten lassen sich auf der '
                              'Seite der App hochladen (Web-UI öffnen, „Ältere Daten importieren“).',
        'no_data.title': 'LINZ NETZ Energy: keine Daten',
        'no_data.text': 'Das Portal hat keine Werte für diesen Zählpunkt. Ist im Portal die Viertelstundenauswertung '
                        'eingeschaltet?',
        'download_failed.title': 'LINZ NETZ Energy: erster Download fehlgeschlagen',
        'download_failed.text': 'Das Portal hat nicht wie erwartet geantwortet ({error}). Die App versucht es in einer '
                                'Stunde wieder.',
        'login_failed.title': 'LINZ NETZ Energy: Portal-Login fehlgeschlagen',
        'login_failed.text': 'Das LINZ NETZ Portal hat den Login abgelehnt. Bitte Portal-Benutzername und -Passwort im '
                             'Tab Konfiguration der App prüfen. Nächster Versuch in {hours} Stunden.',
        'import.title': 'LINZ NETZ Energy: ältere Daten importiert',
        'import.readings': '{file}: {added} von {total} Werten ergänzt',
        'import.reconstructed': '{file}: {rows} rekonstruierte Viertelstunden übernommen',
        'import.failed': '{file}: nicht importiert ({reason})',
        'import.other_meter': 'die Datei gehört zu einem anderen Zählpunkt',
        'import.bad_header': 'die Datei hat nicht die erwarteten Spalten',
        'import.unknown_file': 'importiert werden nur <Zählpunkt>_readings.csv und <Zählpunkt>_reconstructed_15min.csv',
        'stat.consumption': '{site} Netzbezug',
        'stat.cost': '{site} Stromkosten',
        'import.prices': '{file}: {rows} Preise übernommen ({rejected} Zeilen nicht lesbar)',
        'page.costs': 'Kosten',
        'page.costs_lead': 'Was der Verbrauch kostet, wie auf den Rechnungen von Netzbetreiber und Lieferant zusammen: '
                           'Energiepreis und Gebühren des Lieferanten, Netzentgelte und Abgaben, Umsatzsteuer.',
        'page.costs_grid': 'Netzbetreiber',
        'page.costs_grid_unknown': 'für diesen Netzbetreiber noch keine Preise: nur der Lieferant wird gerechnet',
        'page.costs_supplier': 'Lieferant',
        'page.costs_no_contract': 'noch nicht festgelegt: unten auswählen',
        'page.costs_since': '{product} seit {start}',
        'page.costs_price': 'Preis jetzt',
        'page.costs_price_line': '{gross} ct/kWh inkl. Netz, Abgaben und USt. (Energiepreis {energy} ct/kWh netto), dazu Fixkosten',
        'page.costs_price_none': 'für diesen Monat noch kein Energiepreis',
        'page.costs_fetch_error': 'Abrufen des Preises fehlgeschlagen: {error}; die App versucht es erneut',
        'page.costs_price_pending': 'der Lieferant hat den Preis für diesen Monat noch nicht veröffentlicht; die App sieht alle 6 Stunden nach',
        'page.costs_years': 'Pro Jahr',
        'page.costs_last12': 'letzte 12 Monate: {eur} €',
        'page.costs_unpriced': 'Monate ohne Energiepreis',
        'page.costs_unpriced_hint': 'In diesen Monaten zählen nur Netzentgelte, Abgaben und Gebühren. Eine Preisliste füllt '
                                    'sie (siehe Dokumentation, „Costs“).',
        'page.costs_statistic': 'Statistik',
        'page.costs_statistic_line': '{eur} € seit {first}',
        'page.contract': 'Lieferant festlegen',
        'page.contract_supplier': 'Lieferant',
        'page.contract_product': 'Produkt',
        'page.contract_start': 'Gültig ab',
        'page.contract_base_fee': 'Grundpauschale (EUR pro Monat, netto)',
        'page.contract_online_bonus': 'Online-Bonus (EUR pro Monat, netto, negativ)',
        'page.contract_energy': 'Energiepreis (ct/kWh, netto)',
        'page.contract_hint': 'Ein neuer Lieferant ab einem Datum beendet den bisherigen; die Kosten davor bleiben, wie sie waren.',
        'page.contract_save': 'Speichern',
        'page.contract_requested': 'Wird gespeichert, die Seite aktualisiert sich selbst.',
        'page.contract_saved': 'Gespeichert. Die Kosten werden neu berechnet.',
        'page.contract_invalid': 'Nicht gespeichert: bitte Datum und Zahlen prüfen.',
        'deviation.title': 'LINZ NETZ Energy: Live-Zähler und Portal weichen ab',
        'deviation.text': 'An diesen Tagen weichen die Live-Werte des Zählers um mehr als 2 % vom Portal ab '
                          '(Live / Portal): {details}. Die Live-Werte bleiben; bitte die Live-Quelle (etwa den Empfänger) und den Zähler prüfen.',
        'entity.last_portal_fetch': 'Letzter Portal-Abruf',
        'entity.portal_data_until': 'Portaldaten bis',
        'entity.live_data_until': 'Live-Daten bis',
        'entity.live_deviation': 'Live-Abweichung letzter Tag',
        'entity.deviating_hours': 'Abweichende Stunden letzte 7 Tage',
        'entity.portal_login_problem': 'Portal-Login-Problem',
        'phase.live': 'liest den Live-Zählerstand',
        'page.live': 'Live-Zählerwerte',
        'page.live_hours': '{count} Stunden, {first} bis {last}',
        'page.live_check': 'Live vs. Portal',
        'page.live_check_line': 'letzter vollständiger Tag {day}: {percent} %; {hours} abweichende Stunden in 7 Tagen',
        'page.live_check_none': 'noch kein Tag, den beide vollständig haben',
        'page.live_meter': 'Live-Zähler',
        'page.live_meter_lead': 'Der Live-Zählerstand des Zählers in Home Assistant wird zur Hauptquelle der '
                                'Verbrauchsstatistik; das Portal prüft ihn und füllt Lücken.',
        'page.live_meter_none': 'Keiner (nur Portal)',
        'page.live_meter_save': 'Speichern',
        'page.live_meter_no_candidates': 'Home Assistant hat noch keinen Sensor, der Energie aufwärts zählt (kWh).',
        'page.live_meter_requested': 'Wird gespeichert, die Seite aktualisiert sich selbst.',
        'page.live_meter_saved': 'Gespeichert: {entity}. Er gilt ab sofort.',
        'page.live_meter_unknown': '{entity} ist kein Sensor, der Energie aufwärts zählt.',
        'page.energy': 'Energie-Dashboard',
        'page.energy_ok': 'Nutzt **{name}** als aus dem Netz bezogene Energie.',
        'page.energy_double': 'Es nutzt zusätzlich {others}: bitte dort entfernen, sonst wird der Verbrauch doppelt gezählt.',
        'page.energy_switch': '**{name}** enthält den ganzen Verlauf, die Live-Zählerwerte und die vom Portal gefüllten '
                              'Lücken. So nutzt du sie im Energie-Dashboard:',
        'page.fetch_now': 'Jetzt abrufen',
        'page.fetch_requested': 'Wird vom Portal abgerufen, die Seite aktualisiert sich selbst.',
        'page.live_meter_in_use': 'In Verwendung',
        'page.live_power': 'Leistung',
        'page.live_power_hint': 'vom selben Gerät; sie teilt den Zählerstand auf, wenn der Zähler eine Weile keinen '
                                'verwertbaren Wert sendet',
        'page.live_power_none': 'kein Leistungssensor am selben Gerät: in Empfangslücken wird der Verbrauch gleichmäßig '
                                'aufgeteilt (eine Schätzung, bis das Portal die Stunden hat)',
        'page.live_meter_choose': 'Zähler wählen',
        'page.energy_uses': 'Netzbezug derzeit',
        'page.energy_step_backup': '**Einstellungen > Backup**: ein Backup erstellen.',
        'page.energy_step_open': '**Einstellungen > Energie**, Abschnitt **Stromnetz**: beim Netzanschluss '
                                 '**Netzanschluss bearbeiten** (Stift) öffnen.',
        'page.energy_step_pick': 'Bei **Aus dem Netz bezogene Energie** „{name}“ statt {current} wählen.',
        'page.energy_step_cost': 'Bei **Kostenerfassung** **Eine Entität verwenden, die die Gesamtkosten erfasst** wählen '
                                 'und „{cost}“ auswählen, dann speichern.',
        'page.energy_step_save': 'Speichern.',
        'page.energy_step_add': '**Netzanschluss hinzufügen** wählen, bei **Aus dem Netz bezogene Energie** „{name}“ '
                                'auswählen.',
        'page.energy_undo': 'Lässt sich jederzeit auf dem gleichen Weg rückgängig machen; die Statistiken des alten '
                            'Sensors bleiben.',
        'page.energy_none': 'noch nichts',
        'page.energy_unknown': 'Die Einstellungen des Energie-Dashboards ließen sich nicht lesen.',
        'page.remove_title': 'Alles aus Home Assistant entfernen',
        'page.remove_text': 'Vor dem Deinstallieren: das löscht, was die App in Home Assistant angelegt hat (die '
                            'Statistik, das Gerät mit seinen Status-Entitäten, ihre Benachrichtigungen) und pausiert die '
                            'App. Ihre eigenen Dateien bleiben, bis du sie mit „Daten löschen“ deinstallierst. Nicht '
                            'möglich, solange das Energie-Dashboard die Statistik nutzt.',
        'page.remove_check': 'Mir ist klar, dass die Statistik mit dem ganzen Verlauf aus Home Assistant gelöscht wird.',
        'page.remove_confirm': 'Statistik, Gerät und Benachrichtigungen der App löschen?',
        'page.remove_button': 'Alles entfernen',
        'page.remove_unconfirmed': 'Bitte zur Bestätigung das Kästchen anhaken.',
        'page.remove_requested': 'Wird entfernt, die Seite aktualisiert sich selbst.',
        'page.remove_refused': 'Das Energie-Dashboard nutzt die Statistik noch. Bitte dort zuerst entfernen '
                               '(Einstellungen > Dashboards > Energie), dann nochmals versuchen.',
        'page.remove_failed': 'Entfernen hat nicht geklappt ({error}), nichts ist pausiert. Details stehen im Protokoll; '
                              'bitte später nochmals versuchen.',
        'page.removed_title': 'Aus Home Assistant entfernt',
        'page.removed_text': 'Statistik, Gerät und Benachrichtigungen sind weg, die App ist pausiert. Jetzt deinstallieren '
                             'und „Daten löschen“ anhaken, damit auch ihre Dateien weg sind. Doch nicht? Reaktivieren: '
                             'alles wird innerhalb einer Stunde neu angelegt.',
        'page.resume': 'Reaktivieren',
        'page.resume_requested': 'Wird reaktiviert, die Seite aktualisiert sich selbst.',
        'phase.removing': 'entfernt',
        'phase.removed': 'entfernt, pausiert',
        'page.not_configured': 'Noch nicht eingerichtet',
        'page.site': 'Standort {site} · Zählpunkt {metering_point}',
        'source.live': 'Live-Zähler',
        'source.portal': 'Portal',
        'source.portal_substitute': 'Portal-Ersatzwerte',
        'source.reconstructed': 'rekonstruiert',
        'source.portal_partial': 'Portal, unvollständige Stunden',
        'page.setup_title': 'Einrichtung nötig',
        'page.status': 'Status',
        'page.state': 'Zustand',
        'page.last_run': 'Letzter Lauf',
        'page.next_run': 'Nächster Lauf',
        'page.portal_login': 'Portal-Login',
        'page.last_fetch': 'Letzter Portal-Abruf',
        'page.portal_next': 'Nächster Portal-Abruf',
        'page.portal_next_text': '{time} (der gestrige Tag fehlt im Portal noch; zwischen 06:00 und 22:00 wird stündlich nachgesehen)',
        'page.portal_next_done': '{time} (der gestrige Tag ist vollständig; der nächste wird ab 06:00 gesucht)',
        'page.portal_usual': 'Neue Tage kommen',
        'page.portal_usual_text': 'meist gegen {time} (letzte {count} Tage)',
        'page.never': 'noch nie',
        'page.not_tried': 'noch nicht versucht',
        'page.login_ok': 'ok',
        'page.login_rejected': 'abgelehnt',
        'page.data': 'Daten',
        'page.readings': 'Portalwerte',
        'page.reconstructed': 'Rekonstruierte Daten',
        'page.none': 'keine',
        'page.quarter_hours': '{count} Viertelstunden, {first} bis {last}',
        'page.statistics': 'Statistiken',
        'page.stat_consumption': 'Netzbezug (für das Energie-Dashboard)',
        'page.not_written': 'noch nicht geschrieben',
        'page.stat_line': '{hours} Stunden, {first} bis {last}, {kwh} kWh',
        'page.deviating': '{count} abweichende Stunden',
        'page.estimated': '{count} geschätzte Stunden',
        'page.estimated_hint': 'Der Live-Zähler hatte an der Stundengrenze eine Empfangslücke (der Zähler sendet ab und '
                               'zu etwa eine halbe Stunde lang keinen verwertbaren Zählerstand), daher ist die Aufteilung '
                               'auf die beiden Stunden geschätzt; ihre Summe stimmt. Der Portalwert ersetzt sie ein bis '
                               'zwei Tage später.',
        'page.gaps': 'Lücken',
        'page.gaps_none': 'keine',
        'page.gap_line': '{first} bis {last} ({hours} Stunden)',
        'page.gaps_more': 'und {count} weitere',
        'page.gaps_hint': 'Für diese Stunden hat keine Quelle Werte. Ältere Messwerte (etwa die readings.csv der '
                          'homebase-Werkzeuge) füllen sie über „Ältere Daten importieren“ unten.',
        'page.import_title': 'Ältere Daten importieren',
        'page.import_lead': 'Das Portal hält nur etwa 36 Monate. Ältere Daten aus den homebase-Werkzeugen lassen sich '
                            'hier ergänzen; die Dateien werden geprüft und sofort importiert, dieselbe Datei nochmals '
                            'zu importieren ändert nichts.',
        'page.metering_point': 'Zählpunkt',
        'page.file_readings': 'ältere Portalwerte, werden Zeile für Zeile ergänzt',
        'page.file_reconstructed': 'die rekonstruierten Daten (6-Stunden-Werte auf Viertelstunden aufgeteilt)',
        'page.drop': 'Dateien auswählen oder hierher ziehen',
        'page.drop_hint': 'CSV-Dateien, gerne beide auf einmal',
        'page.selected': '{count} Datei(en) ausgewählt',
        'page.upload': 'Hochladen und importieren',
        'page.import_pending': 'Die Dateien werden importiert, die Seite aktualisiert sich selbst.',
        'page.last_import': 'Letzter Import',
        'page.nothing_imported': 'Noch nichts importiert.',
        'page.uploaded': 'Hochgeladen: {files}',
        'page.too_large': 'Die Datei ist zu groß.',
        'page.no_file': 'Es wurde keine Datei hochgeladen.',
        'phase.idle': 'aktuell',
        'phase.starting': 'startet',
        'phase.importing': 'importiert',
        'phase.downloading': 'lädt vom Portal',
        'phase.statistics': 'schreibt Statistiken',
        'phase.setup': 'Einrichtung nötig',
        'phase.error': 'Fehler',
        'detail.first_download': 'erster Download, etwa eine Minute',
        'detail.new_readings': 'neue Werte',
        'detail.older_history': 'ältere Daten',
    },
}


def language(value: str | None) -> str:
    """'de', 'de-AT' -> 'de'; anything without texts -> 'en'."""
    code = (value or 'en').split('-')[0].split('_')[0].lower()
    return code if code in TEXTS else 'en'


def t(lang: str, key: str, **values) -> str:
    text = TEXTS[language(lang)].get(key) or TEXTS['en'][key]
    return text.format(**values) if values else text
