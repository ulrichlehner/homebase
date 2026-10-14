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
        'problem.site': 'The site may only contain letters, digits and _.',
        # notifications
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
        'page.not_configured': 'Not configured yet',
        'page.site': 'Site {site} · metering point {metering_point}',
        'source.rf': 'radio (RF)',
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
        'page.stat_portal': 'Portal readings only (for checks)',
        'page.not_written': 'not written yet',
        'page.stat_line': '{hours} hours, {first} to {last}, {kwh} kWh',
        'page.deviating': '{count} deviating hours',
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
        'problem.site': 'Der Standort darf nur Buchstaben, Ziffern und _ enthalten.',
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
        'page.not_configured': 'Noch nicht eingerichtet',
        'page.site': 'Standort {site} · Zählpunkt {metering_point}',
        'source.rf': 'Funk (RF)',
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
        'page.stat_portal': 'Nur Portalwerte (zur Kontrolle)',
        'page.not_written': 'noch nicht geschrieben',
        'page.stat_line': '{hours} Stunden, {first} bis {last}, {kwh} kWh',
        'page.deviating': '{count} abweichende Stunden',
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
