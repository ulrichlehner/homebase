"""The page of the add-on "LINZ NETZ Energy" in Home Assistant (Ingress): status and history upload.

Home Assistant shows it behind the "Open Web UI" button of the add-on (and optionally in the
sidebar); HA handles the login and proxies the requests. The server therefore only answers
the Ingress proxy (172.30.32.2) and uses relative links, so it works under any Ingress path.

  GET  /         the page: what the add-on does right now, data ranges, statistics, last
                 import; refreshes itself while something is running
  POST /upload   history files (multipart form, field `files`): stored in import/ and
                 imported on the spot by waking the main loop
  POST /live_meter  the live meter picked from the list (field `entity`, empty: none); the
                 main loop keeps it in the add-on's state
  POST /fetch    fetch the portal now (the main loop does it right away)
  POST /remove, /resume  remove everything from Home Assistant, or reactivate

Every POST answers with a redirect back to the page (its message shown once there), so the
page's own refresh and a browser reload never land on an action address.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import tempfile
import threading
from urllib.parse import parse_qs
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from texts import TEXTS, language, t

logger = logging.getLogger('addon.web')

INGRESS_PROXY = '172.30.32.2'
PORT = 8099
MAX_UPLOAD = 64 * 1024 * 1024   # the readings file of three years is about 8 MB
STATUS_FILE = 'status.json'


# -- Status shared with the main loop ---------------------------------------------------

def read_status(store: Path) -> dict:
    try:
        return json.loads((store / STATUS_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {}


_status_lock = threading.Lock()


def write_status(store: Path, **changes) -> dict:
    """Merges `changes` into status.json (atomically) and returns the new status. The main loop
    and the page's request threads both write it: the lock keeps one read-merge-write from
    overtaking another (which would lose changes), and every write has its own temporary file."""
    with _status_lock:
        status = read_status(store)
        status.update(changes)
        fd, tmp = tempfile.mkstemp(dir=store, prefix=STATUS_FILE + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as fh:
                fh.write(json.dumps(status, indent=2, sort_keys=True, default=str))
            os.replace(tmp, store / STATUS_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return status


# -- Upload -------------------------------------------------------------------------------

def parse_upload(content_type: str, body: bytes) -> list:
    """[(file name, bytes)] of the file parts of a multipart/form-data body."""
    message = BytesParser(policy=email_policy).parsebytes(
        b'Content-Type: ' + content_type.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    files = []
    if not message.is_multipart():
        return files
    for part in message.iter_parts():
        name = part.get_filename()
        if name:
            files.append((Path(name).name, part.get_payload(decode=True) or b''))
    return files


def store_upload(import_dir: Path, name: str, data: bytes) -> Path:
    """Writes one uploaded file into import/ atomically: the main loop only picks up *.csv,
    so it never sees a half-written file."""
    import_dir.mkdir(parents=True, exist_ok=True)
    safe = ''.join(c for c in name if c.isalnum() or c in '._-') or 'upload.csv'
    tmp = import_dir / f'.{safe}.part'
    tmp.write_bytes(data)
    target = import_dir / safe
    tmp.replace(target)
    return target


# -- Page ---------------------------------------------------------------------------------

STYLE = """
:root { --primary: #03a9f4; --primary-soft: rgba(3, 169, 244, .08); --bg: #fafafa; --card: #ffffff;
        --text: #212121; --muted: #727272; --divider: #e0e0e0; --code-bg: rgba(0, 0, 0, .05); --ok: #43a047; --warn: #ffa000; --err: #db4437; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111111; --card: #1c1c1c; --text: #e1e1e1; --muted: #9b9b9b; --divider: #2f2f2f; --code-bg: rgba(255, 255, 255, .08);
          --primary-soft: rgba(3, 169, 244, .12); } }
* { box-sizing: border-box; }
/* The HA app shows the page in an iframe; on iOS such an iframe only scrolls when the page
   scrolls itself, not the window: html stays fixed, body is the scroll container. */
html { height: 100%; overflow: hidden; }
body { height: 100%; overflow-y: auto; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--text);
       font: 16px/1.55 Roboto, "Noto Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 880px; margin: 0 auto; display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }
/* long unbreakable values (metering point, statistic IDs, file names) wrap instead of widening the page */
main, code, td, li { overflow-wrap: anywhere; }
.card { background: var(--card); border-radius: 12px; border: 1px solid var(--divider); padding: 20px 24px; }
h1 { font-size: 28px; font-weight: 400; margin: 0; }
h2 { font-size: 20px; font-weight: 500; margin: 0 0 14px; }
p { margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; }
td, th { text-align: left; padding: 10px 12px 10px 0; border-bottom: 1px solid var(--divider); vertical-align: top; }
th { font-weight: 500; color: var(--muted); width: 36%; }
tr:last-child td, tr:last-child th { border-bottom: 0; }
.muted { color: var(--muted); }
.pill { display: inline-block; border-radius: 12px; padding: 1px 10px; font-size: 14px; color: #fff; }
.ok { background: var(--ok); } .warn { background: var(--warn); } .err { background: var(--err); }
.run { background: var(--primary); }
.setup { border-left: 4px solid var(--warn); }
.danger { border-left: 4px solid var(--err); }
/* destructive and secondary actions: outlined and only as wide as their text, the upload stays the main action */
button.danger, button.secondary { display: inline-block; width: auto; background: transparent; font-size: 16px;
                                  padding: 10px 18px; }
button.danger { color: var(--err); border: 1px solid var(--err); }
button.secondary { color: var(--primary); border: 1px solid var(--primary); }
.lead { color: var(--muted); }
form.inline { display: inline; margin-left: 12px; }
button.small { padding: 4px 12px; font-size: 14px; margin: 0; vertical-align: baseline; }
.pick { margin-top: 16px; }
.pick label { display: block; font-weight: 500; color: var(--muted); font-size: 14px; margin-bottom: 6px; }
.pick .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: stretch; }
.pick select { flex: 1 1 260px; min-width: 0; height: 48px; font: inherit; color: var(--text); background: var(--card);
               border: 1px solid var(--divider); border-radius: 8px; padding: 0 12px; }
.pick select:focus { outline: 2px solid var(--primary); outline-offset: -1px; }
.pick button { flex: 0 0 auto; width: auto; height: 48px; margin: 0; padding: 0 24px; font-size: 16px; }
.steps li::marker { color: var(--muted); font-weight: 500; }
.steps + p { margin-top: 14px; }
.gaps { margin: 6px 0; }
table + p, table + form { margin-top: 14px; }
.check { display: flex; gap: 10px; align-items: flex-start; margin-top: 4px; }
.check input { margin-top: 5px; }
.setup p { margin: 12px 0 0; }
.steps { margin: 12px 0 0; padding-left: 22px; display: grid; gap: 8px; }
.files { margin: 0 0 18px; padding: 0; list-style: none; display: grid; gap: 8px; }
.files li { display: grid; gap: 2px; }
.drop { display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; cursor: pointer;
        padding: 28px 16px; border: 2px dashed var(--divider); border-radius: 12px; background: var(--primary-soft);
        transition: border-color .15s, background .15s; }
.drop:hover, .drop.over { border-color: var(--primary); }
.drop input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.drop svg { width: 40px; height: 40px; fill: var(--primary); }
.drop strong { font-size: 18px; font-weight: 500; }
.chosen { font-weight: 500; color: var(--text); }
button { display: block; width: 100%; margin-top: 14px; background: var(--primary); color: #fff; border: 0;
         border-radius: 8px; padding: 14px 20px; font: inherit; font-size: 17px; font-weight: 500; cursor: pointer; }
button:disabled { opacity: .45; cursor: default; }
ul { margin: 0; padding-left: 20px; }
code { font: 13.5px/1.4 ui-monospace, "SF Mono", Menlo, Consolas, monospace; background: var(--code-bg);
       border-radius: 4px; padding: 1px 5px; }
/* phones: less padding, label above value instead of two table columns */
@media (max-width: 600px) {
  body { padding: 16px 12px; font-size: 15px; }
  main { gap: 14px; }
  .card { padding: 16px; border-radius: 10px; }
  h1 { font-size: 24px; }
  h2 { font-size: 18px; margin-bottom: 10px; }
  table, tbody, tr, th, td { display: block; width: 100%; }
  tr { padding: 10px 0; border-bottom: 1px solid var(--divider); }
  tr:last-child { border-bottom: 0; padding-bottom: 0; }
  th, td { border: 0; padding: 0; }
  th { font-size: 14px; margin-bottom: 2px; }
  .drop { padding: 22px 12px; }
  .drop strong { font-size: 17px; }
  button { padding: 14px 16px; }
  .pick .row { flex-direction: column; }
  .pick select, .pick button { flex: 0 0 48px; width: 100%; }
}
"""


def _fmt_time(value: Optional[str], lang: str) -> str:
    if not value:
        return f'<span class="muted">{t(lang, "page.never")}</span>'
    try:
        when = datetime.fromisoformat(value).astimezone()
        return html.escape(when.strftime('%d.%m.%Y %H:%M'))
    except ValueError:
        return html.escape(value)


def _rows(pairs: list) -> str:
    return ''.join(f'<tr><th>{html.escape(k)}</th><td>{v}</td></tr>' for k, v in pairs)


def _text(lang: str, entry) -> str:
    """A stored (text key, values) pair in the page language; plain strings stay as they are."""
    if isinstance(entry, str):
        return entry
    key, values = entry
    values = dict(values)
    if 'reason_key' in values:
        values['reason'] = t(lang, values.pop('reason_key'))
    return t(lang, key, **values)


def _rich(text: str) -> str:
    """Escapes a text and turns `x` into <code>x</code> and **x** into <strong>x</strong>, so the
    texts can name entities and UI paths readably."""
    out = html.escape(text, quote=False)
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', out)


def render_page(status: dict, message=None) -> str:
    """The page in the language of HA (`status['language']`). `message` is a (text key,
    values) pair shown on top, e.g. the result of an upload."""
    lang = language(status.get('language'))
    phase = status.get('phase', 'idle')
    pill_class = {'idle': 'ok', 'importing': 'run', 'downloading': 'run', 'statistics': 'run', 'live': 'run',
                  'removed': 'err', 'removing': 'run',
                  'starting': 'run', 'setup': 'warn', 'error': 'err'}.get(phase, 'warn')
    pill_text = t(lang, f'phase.{phase}') if f'phase.{phase}' in TEXTS['en'] else phase
    detail = status.get('detail', '')
    detail = t(lang, detail) if detail in TEXTS['en'] else detail
    busy = (phase not in ('idle', 'setup', 'removed') or status.get('import_pending') or status.get('removal_request')
            or status.get('live_meter_request') or status.get('fetch_request'))
    refresh = '<meta http-equiv="refresh" content="5">' if busy else ''

    setup = status.get('setup') or []
    setup_html = ''
    if setup:
        items = ''.join(f'<li>{html.escape(_text(lang, line))}</li>' for line in setup)
        setup_html = (f'<div class="card setup"><h2>{t(lang, "page.setup_title")}</h2><ul>{items}</ul>'
                      f'<p>{html.escape(t(lang, "setup.howto"))}</p></div>')

    if status.get('password_secret') is False:
        steps = ''.join(f'<li>{_rich(t(lang, f"secret.step{i}"))}</li>' for i in range(1, 7))
        setup_html += (f'<div class="card setup"><h2>{t(lang, "page.secret_title")}</h2>'
                       f'<p>{_rich(t(lang, "secret.intro"))}</p><ol class="steps">{steps}</ol>'
                       f'<p class="muted">{html.escape(t(lang, "secret.outro"))}</p></div>')

    portal = status.get('portal', {})
    fetching = status.get('fetch_request') or phase == 'downloading'
    fetch_button = ('' if status.get('setup') or phase == 'removed' else
                    f'<form method="post" action="fetch" class="inline"><button type="submit" class="secondary small"'
                    f'{" disabled" if fetching else ""}>{t(lang, "page.fetch_now")}</button></form>')
    if portal.get('login_failed'):
        login = (f'<span class="pill err">{t(lang, "page.login_rejected")}</span> '
                 + _fmt_time(portal.get('login_failed'), lang))
    elif portal.get('ok'):
        login = f'<span class="pill ok">{t(lang, "page.login_ok")}</span>'
    else:
        login = f'<span class="muted">{t(lang, "page.not_tried")}</span>'
    data = status.get('data', {})
    stats = status.get('statistics', {})

    def stat_row(key: str) -> str:
        s = stats.get(key)
        if not s:
            return f'<span class="muted">{t(lang, "page.not_written")}</span>'
        sources = ', '.join(f'{t(lang, "source." + k) if "source." + k in TEXTS["en"] else k} {v}'
                            for k, v in s.get('sources', {}).items())
        flagged = (f' · <span class="pill warn">{t(lang, "page.deviating", count=s["flagged"])}</span>'
                   if s.get('flagged') else '')
        line = t(lang, 'page.stat_line', hours=s['hours'], first=s['from'], last=s['to'], kwh=f'{s["kwh"]:.1f}')
        return (f'<code>{html.escape(s["id"])}</code><br>{html.escape(line)}<br>'
                f'<span class="muted">{html.escape(sources)}</span>{flagged}')

    def gaps_row(key: str) -> str:
        gaps = (stats.get(key) or {}).get('gaps') or []
        if not gaps:
            return f'<span class="muted">{t(lang, "page.gaps_none")}</span>'
        shown = ''.join(f'<li>{html.escape(t(lang, "page.gap_line", first=g["from"], last=g["to"], hours=g["hours"]))}</li>'
                        for g in gaps[:5])
        more = f'<li class="muted">{html.escape(t(lang, "page.gaps_more", count=len(gaps) - 5))}</li>' if len(gaps) > 5 else ''
        return (f'<span class="pill warn">{len(gaps)}</span><ul class="gaps">{shown}{more}</ul>'
                f'<span class="muted">{html.escape(t(lang, "page.gaps_hint"))}</span>')

    def range_row(d: Optional[dict]) -> str:
        if not d:
            return f'<span class="muted">{t(lang, "page.none")}</span>'
        return html.escape(t(lang, 'page.quarter_hours', count=d['count'], first=d['from'], last=d['to']))

    def live_row(d: Optional[dict]) -> str:
        if not d:
            return f'<span class="muted">{t(lang, "page.none")}</span>'
        return html.escape(t(lang, 'page.live_hours', count=d['count'], first=d['from'], last=d['to']))

    def check_row() -> str:
        c = status.get('live_check') or {}
        if not c.get('last_day'):
            return f'<span class="muted">{t(lang, "page.live_check_none")}</span>'
        line = html.escape(t(lang, 'page.live_check_line', day=c['last_day'], percent=c['last_day_percent'],
                             hours=c['deviating_hours']))
        bad = (f' <span class="pill warn">{len(c["bad_days"])} × &gt; 2 %</span>' if c.get('bad_days') else '')
        return line + bad

    removal = status.get('removal') or {}
    if removal.get('state') == 'done':
        remove_html = (f'<div class="card danger"><h2>{t(lang, "page.removed_title")}</h2>'
                       f'<p>{html.escape(t(lang, "page.removed_text"))}</p>'
                       f'<form method="post" action="resume"><button type="submit" class="secondary">'
                       f'{t(lang, "page.resume")}</button></form></div>')
    else:
        problem = {'refused': t(lang, 'page.remove_refused'),
                   'failed': t(lang, 'page.remove_failed', error=removal.get('error', ''))}.get(removal.get('state'))
        refused = f'<p><span class="pill warn">!</span> {html.escape(problem)}</p>' if problem else ''
        remove_html = (f'<div class="card danger"><h2>{t(lang, "page.remove_title")}</h2>'
                       f'<p>{html.escape(t(lang, "page.remove_text"))}</p>{refused}'
                       f'<form method="post" action="remove" onsubmit="return confirm({html.escape(json.dumps(t(lang, "page.remove_confirm")))})">'
                       f'<label class="check"><input type="checkbox" name="confirm" value="yes" required> '
                       f'{html.escape(t(lang, "page.remove_check"))}</label>'
                       f'<button type="submit" class="danger">{t(lang, "page.remove_button")}</button></form></div>')
    live_meter_html = ''
    if 'live_candidates' in status:
        current = status.get('live_meter') or ''
        candidates = status.get('live_candidates') or []
        by_id = {c['entity_id']: c for c in candidates}
        choices = [('', t(lang, 'page.live_meter_none'))] + [
            (c['entity_id'], f'{c["name"]} ({c["state"]} {c["unit"]})') for c in candidates]
        if current and current not in by_id:
            choices.append((current, current))   # chosen earlier, no longer a matching sensor
        select = ''.join(f'<option value="{html.escape(v)}"{" selected" if v == current else ""}>{html.escape(label)}</option>'
                         for v, label in choices)
        if current:
            c = by_id.get(current)
            value = f' · {html.escape(str(c["state"]))} {html.escape(c["unit"])}' if c else ''
            name = f'<strong>{html.escape(c["name"])}</strong><br>' if c else ''
            in_use = f'{name}<code>{html.escape(current)}</code>{value}'
        else:
            in_use = f'<span class="muted">{html.escape(t(lang, "page.live_meter_none"))}</span>'
        result = status.get('live_meter_result') or {}
        result_text = {'saved': lambda: t(lang, 'page.live_meter_saved', entity=result.get('entity') or t(lang, 'page.live_meter_none')),
                       'unknown': lambda: t(lang, 'page.live_meter_unknown', entity=result.get('entity', ''))}.get(result.get('state'))
        result_html = (f'<p><span class="pill {"ok" if result.get("state") == "saved" else "warn"}">'
                       f'{"ok" if result.get("state") == "saved" else "!"}</span> {html.escape(result_text())}</p>'
                       if result_text else '')
        empty = f'<p class="muted">{html.escape(t(lang, "page.live_meter_no_candidates"))}</p>' if not candidates else ''
        live_meter_html = (f'<div class="card"><h2>{t(lang, "page.live_meter")}</h2>'
                           f'<p class="lead">{html.escape(t(lang, "page.live_meter_lead"))}</p>'
                           f'<table>{_rows([(t(lang, "page.live_meter_in_use"), in_use)])}</table>{empty}{result_html}'
                           f'<form method="post" action="live_meter" class="pick">'
                           f'<label for="live-meter">{t(lang, "page.live_meter_choose")}</label>'
                           f'<div class="row"><select name="entity" id="live-meter">{select}</select>'
                           f'<button type="submit">{t(lang, "page.live_meter_save")}</button></div></form></div>')

    energy = status.get('energy')
    energy_html = ''
    if 'energy' in status:
        name = t(lang, 'stat.consumption', site=status.get('site', ''))
        if not energy:
            body = f'<p class="muted">{html.escape(t(lang, "page.energy_unknown"))}</p>'
        elif energy.get('uses_consumption'):
            others = [g for g in energy['grid_from'] if g != energy['consumption_statistic']]
            body = f'<p><span class="pill ok">ok</span> {_rich(t(lang, "page.energy_ok", name=name))}</p>'
            if others:
                body += (f'<p><span class="pill warn">!</span> '
                         f'{_rich(t(lang, "page.energy_double", others=", ".join(f"`{o}`" for o in others)))}</p>')
        else:
            current = ', '.join(f'`{g}`' for g in energy['grid_from'])
            used = _rich(current) if current else f'<span class="muted">{t(lang, "page.energy_none")}</span>'
            keys = ('page.energy_step_backup', 'page.energy_step_open',
                    'page.energy_step_pick' if current else 'page.energy_step_add')
            steps = ''.join(f'<li>{_rich(t(lang, k, name=name, current=current))}</li>' for k in keys)
            body = (f'<table>{_rows([(t(lang, "page.energy_uses"), used)])}</table>'
                    f'<p class="lead">{_rich(t(lang, "page.energy_switch", name=name))}</p>'
                    f'<ol class="steps">{steps}</ol>'
                    f'<p class="muted">{html.escape(t(lang, "page.energy_undo"))}</p>')
        energy_html = f'<div class="card"><h2>{t(lang, "page.energy")}</h2>{body}</div>'

    status_rows = [
        (t(lang, 'page.state'), f'<span class="pill {pill_class}">{html.escape(pill_text)}</span> {html.escape(detail)}'),
        (t(lang, 'page.last_run'), _fmt_time(status.get('last_run'), lang)),
        (t(lang, 'page.next_run'), _fmt_time(status.get('next_run'), lang)),
        (t(lang, 'page.portal_login'), login),
        (t(lang, 'page.last_fetch'), _fmt_time(portal.get('ok'), lang) + fetch_button),
    ]
    if portal.get('next'):
        key = 'page.portal_next_text' if portal.get('next_waits') else 'page.portal_next_done'
        status_rows.append((t(lang, 'page.portal_next'), t(lang, key, time=_fmt_time(portal['next'], lang))))
    if portal.get('usual'):
        status_rows.append((t(lang, 'page.portal_usual'),
                            html.escape(t(lang, 'page.portal_usual_text', time=portal['usual'], count=portal.get('arrivals', 0)))))

    last_import = status.get('last_import') or {}
    import_lines = ''.join(f'<li>{html.escape(_text(lang, line))}</li>' for line in last_import.get('lines', []))
    import_html = (f'<p class="muted">{_fmt_time(last_import.get("at"), lang)}</p><ul>{import_lines}</ul>'
                   if import_lines else f'<p class="muted">{t(lang, "page.nothing_imported")}</p>')
    note = f'<div class="card"><strong>{html.escape(_text(lang, message))}</strong></div>' if message else ''
    pending = (f'<p><span class="pill run">{t(lang, "phase.importing")}</span> {t(lang, "page.import_pending")}</p>'
               if status.get('import_pending') else '')

    if status.get('site'):
        point = status.get('metering_point') or ('…' + status.get('meter_suffix', ''))
        site_line = html.escape(t(lang, 'page.site', site=status['site'], metering_point=point))
    else:
        site_line = html.escape(t(lang, 'page.not_configured'))
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>LINZ NETZ Energy</title><style>{STYLE}</style></head>
<body><main>
<div><h1>LINZ NETZ Energy</h1><p class="muted">{site_line}</p></div>
{note}
{setup_html}
<div class="card"><h2>{t(lang, 'page.status')}</h2><table>{_rows(status_rows)}</table></div>
<div class="card"><h2>{t(lang, 'page.data')}</h2><table>{_rows([
        (t(lang, 'page.readings'), range_row(data.get('readings'))),
        (t(lang, 'page.reconstructed'), range_row(data.get('reconstructed'))),
    ] + ([(t(lang, 'page.live'), live_row(data.get('live'))), (t(lang, 'page.live_check'), check_row())]
         if 'live' in data and (data.get('live') or status.get('live_check')) else []))}</table></div>
<div class="card"><h2>{t(lang, 'page.statistics')}</h2><table>{_rows([
        (t(lang, 'page.stat_consumption'), stat_row('consumption')),
    ] + ([(t(lang, 'page.gaps'), gaps_row('consumption'))] if stats.get('consumption') else []) + [
    ])}</table></div>
{live_meter_html}
{energy_html}
<div class="card"><h2>{t(lang, 'page.import_title')}</h2>
<p>{t(lang, 'page.import_lead')}</p>
<ul class="files">
<li><code>&lt;{t(lang, 'page.metering_point')}&gt;_readings.csv</code><span class="muted">{t(lang, 'page.file_readings')}</span></li>
<li><code>&lt;{t(lang, 'page.metering_point')}&gt;_reconstructed_15min.csv</code><span class="muted">{t(lang, 'page.file_reconstructed')}</span></li>
</ul>
{pending}
<form method="post" action="upload" enctype="multipart/form-data">
<label class="drop" id="drop">
<input type="file" name="files" id="files" accept=".csv" multiple required>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
<strong id="chosen-title">{t(lang, 'page.drop')}</strong>
<span class="muted" id="chosen">{t(lang, 'page.drop_hint')}</span>
</label>
<button type="submit" id="upload" disabled>{t(lang, 'page.upload')}</button>
</form>
<script>
(function () {{
  var input = document.getElementById('files'), drop = document.getElementById('drop');
  var title = document.getElementById('chosen-title'), names = document.getElementById('chosen');
  var button = document.getElementById('upload');
  function show() {{
    var list = Array.prototype.map.call(input.files, function (f) {{ return f.name; }});
    button.disabled = list.length === 0;
    if (list.length) {{ title.textContent = {json.dumps(t(lang, 'page.selected'))}.replace('{{count}}', list.length);
      names.textContent = list.join(', '); names.className = 'chosen'; }}
  }}
  input.addEventListener('change', show);
  ['dragenter', 'dragover'].forEach(function (e) {{ drop.addEventListener(e, function (ev) {{
    ev.preventDefault(); drop.classList.add('over'); }}); }});
  ['dragleave', 'drop'].forEach(function (e) {{ drop.addEventListener(e, function (ev) {{
    ev.preventDefault(); drop.classList.remove('over'); }}); }});
  drop.addEventListener('drop', function (ev) {{ input.files = ev.dataTransfer.files; show(); }});
}})();
</script>
</div>
<div class="card"><h2>{t(lang, 'page.last_import')}</h2>{import_html}</div>
{remove_html}
</main></body></html>"""


# -- Server -------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    store: Path
    wake: threading.Event
    allow_any = False

    def log_message(self, fmt, *args):
        logger.debug('%s ' + fmt, self.client_address[0], *args)

    def _allowed(self) -> bool:
        if self.allow_any or self.client_address[0] == INGRESS_PROXY:
            return True
        self.send_error(403, 'Only reachable through Home Assistant')
        return False

    def _send(self, code: int, body: str, extra: Optional[dict] = None) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _done(self, message: tuple) -> None:
        """Answers a form with a redirect to the page (303, relative, so it works under any Ingress
        path): the browser shows the page itself, its refresh and reload do not repeat the action.
        The message is shown once on that page."""
        write_status(self.store, notice=[message[0], message[1]])
        self.send_response(303)
        self.send_header('Location', './')
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return
        path = self.path.split('?')[0].rstrip('/')
        if path in ACTIONS:
            # an address left over from before the redirects (or typed): back to the page
            self.send_response(303)
            self.send_header('Location', './')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path not in ('', '/index.html'):
            self.send_error(404)
            return
        status = read_status(self.store)
        notice = status.get('notice')
        if notice:
            write_status(self.store, notice=None)
        self._send(200, render_page(status, tuple(notice) if notice else None))

    def do_POST(self):
        if not self._allowed():
            return
        path = self.path.split('?')[0].rstrip('/')
        if path not in ACTIONS:
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        if path == '/upload':
            if length > MAX_UPLOAD:
                self._done(('page.too_large', {}))
                return
            files = parse_upload(self.headers.get('Content-Type', ''), self.rfile.read(length))
            if not files:
                self._done(('page.no_file', {}))
                return
            names = [store_upload(self.store / 'import', name, data).name for name, data in files]
            write_status(self.store, import_pending=True)
            self.wake.set()
            logger.info('Uploaded %s, importing', ', '.join(names))
            self._done(('page.uploaded', {'files': ', '.join(names)}))
            return
        # the main loop does the work (it holds the connection to HA); the page only asks
        body = self.rfile.read(min(length, 4096)).decode(errors='replace')
        if path in ('/remove', '/resume'):
            if path == '/remove' and 'confirm=yes' not in body:
                self._done(('page.remove_unconfirmed', {}))
                return
            write_status(self.store, removal_request=path.strip('/'))
            self.wake.set()
            logger.info('Requested on the add-on page: %s', path.strip('/'))
            self._done((f'page.{path.strip("/")}_requested', {}))
        elif path == '/fetch':
            write_status(self.store, fetch_request=True)
            self.wake.set()
            logger.info('Requested on the add-on page: portal fetch')
            self._done(('page.fetch_requested', {}))
        elif path == '/live_meter':
            entity = (parse_qs(body).get('entity') or [''])[0].strip()
            if entity and not re.fullmatch(r'sensor\.[a-z0-9_]+', entity):
                self._done(('page.live_meter_unknown', {'entity': entity}))
                return
            write_status(self.store, live_meter_request={'entity': entity}, live_meter_result=None)
            self.wake.set()
            self._done(('page.live_meter_requested', {}))


ACTIONS = ('/upload', '/fetch', '/live_meter', '/remove', '/resume')


def start(store: Path, wake: threading.Event, port: int = PORT) -> ThreadingHTTPServer:
    """Starts the page in a background thread."""
    handler = type('IngressHandler', (Handler,), {
        'store': store, 'wake': wake, 'allow_any': os.environ.get('ADDON_WEB_ALLOW_ANY') == '1'})
    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    threading.Thread(target=server.serve_forever, name='ingress', daemon=True).start()
    logger.info('Add-on page listening on port %d (Ingress)', port)
    return server
