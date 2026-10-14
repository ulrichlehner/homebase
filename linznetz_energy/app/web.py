"""The page of the add-on "LINZ NETZ energy" in Home Assistant (Ingress): status and history upload.

Home Assistant shows it behind the "Open Web UI" button of the add-on (and optionally in the
sidebar); HA handles the login and proxies the requests. The server therefore only answers
the Ingress proxy (172.30.32.2) and uses relative links, so it works under any Ingress path.

  GET  /         the page: what the add-on does right now, data ranges, statistics, last
                 import; refreshes itself while something is running
  POST /upload   history files (multipart form, field `files`): stored in import/ and
                 imported on the spot by waking the main loop
"""

from __future__ import annotations

import html
import json
import logging
import os
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

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


def write_status(store: Path, **changes) -> dict:
    """Merges `changes` into status.json (atomically) and returns the new status."""
    status = read_status(store)
    status.update(changes)
    tmp = store / (STATUS_FILE + '.tmp')
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True, default=str))
    tmp.replace(store / STATUS_FILE)
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
:root { --primary: #03a9f4; --bg: #fafafa; --card: #ffffff; --text: #212121; --muted: #727272;
        --divider: #e0e0e0; --ok: #43a047; --warn: #ffa000; --err: #db4437; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111111; --card: #1c1c1c; --text: #e1e1e1; --muted: #9b9b9b; --divider: #2f2f2f; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--text);
       font: 14px/1.5 Roboto, "Noto Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; display: grid; gap: 16px; }
.card { background: var(--card); border-radius: 12px; border: 1px solid var(--divider); padding: 16px 20px; }
h1 { font-size: 22px; font-weight: 400; margin: 4px 0 0; }
h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; }
td, th { text-align: left; padding: 6px 8px 6px 0; border-bottom: 1px solid var(--divider); vertical-align: top; }
th { font-weight: 500; color: var(--muted); white-space: nowrap; width: 36%; }
tr:last-child td, tr:last-child th { border-bottom: 0; }
.muted { color: var(--muted); }
.pill { display: inline-block; border-radius: 10px; padding: 0 8px; font-size: 12px; color: #fff; }
.ok { background: var(--ok); } .warn { background: var(--warn); } .err { background: var(--err); }
.run { background: var(--primary); }
input[type=file] { display: block; margin: 8px 0 12px; color: var(--text); }
button { background: var(--primary); color: #fff; border: 0; border-radius: 4px; padding: 8px 16px;
         font: inherit; font-weight: 500; text-transform: uppercase; letter-spacing: .04em; cursor: pointer; }
ul { margin: 0; padding-left: 20px; }
code { font-size: 13px; }
"""


def _fmt_time(value: Optional[str]) -> str:
    if not value:
        return '<span class="muted">never</span>'
    try:
        t = datetime.fromisoformat(value).astimezone()
        return html.escape(t.strftime('%d.%m.%Y %H:%M'))
    except ValueError:
        return html.escape(value)


def _rows(pairs: list) -> str:
    return ''.join(f'<tr><th>{html.escape(k)}</th><td>{v}</td></tr>' for k, v in pairs)


def render_page(status: dict, message: str = '') -> str:
    phase = status.get('phase', 'idle')
    pill = {'idle': ('ok', 'up to date'), 'importing': ('run', 'importing'),
            'downloading': ('run', 'downloading from the portal'), 'statistics': ('run', 'writing statistics'),
            'error': ('err', 'error')}.get(phase, ('warn', phase))
    refresh = '<meta http-equiv="refresh" content="5">' if phase != 'idle' or status.get('import_pending') else ''

    portal = status.get('portal', {})
    login = ('<span class="pill err">login rejected</span> ' + _fmt_time(portal.get('login_failed'))
             if portal.get('login_failed') else '<span class="pill ok">ok</span>')
    data = status.get('data', {})
    stats = status.get('statistics', {})

    def stat_row(key: str) -> str:
        s = stats.get(key)
        if not s:
            return '<span class="muted">not written yet</span>'
        sources = ', '.join(f'{k} {v}' for k, v in s.get('sources', {}).items())
        flagged = f' · <span class="pill warn">{s["flagged"]} deviating hours</span>' if s.get('flagged') else ''
        return (f'<code>{html.escape(s["id"])}</code><br>{s["hours"]} hours, {html.escape(s["from"])} to '
                f'{html.escape(s["to"])}, {s["kwh"]:.1f} kWh<br><span class="muted">{html.escape(sources)}</span>{flagged}')

    def range_row(d: Optional[dict], unit: str) -> str:
        if not d:
            return '<span class="muted">none</span>'
        return f'{d["count"]} {unit}, {html.escape(d["from"])} to {html.escape(d["to"])}'

    last_import = status.get('last_import') or {}
    import_lines = ''.join(f'<li>{html.escape(line)}</li>' for line in last_import.get('lines', []))
    import_html = (f'<p class="muted">{_fmt_time(last_import.get("at"))}</p><ul>{import_lines}</ul>'
                   if import_lines else '<p class="muted">Nothing imported yet.</p>')
    note = f'<div class="card"><strong>{html.escape(message)}</strong></div>' if message else ''
    pending = ('<p><span class="pill run">import pending</span> the files are imported in a moment.</p>'
               if status.get('import_pending') else '')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>LINZ NETZ energy</title><style>{STYLE}</style></head>
<body><main>
<div><h1>LINZ NETZ energy</h1><p class="muted">Site {html.escape(status.get('site', '?'))} ·
metering point …{html.escape(status.get('meter_suffix', ''))}</p></div>
{note}
<div class="card"><h2>Status</h2><table>{_rows([
        ('State', f'<span class="pill {pill[0]}">{pill[1]}</span> {html.escape(status.get("detail", ""))}'),
        ('Last run', _fmt_time(status.get('last_run'))),
        ('Next run', _fmt_time(status.get('next_run'))),
        ('Portal login', login),
        ('Last portal fetch', _fmt_time(portal.get('ok'))),
    ])}</table></div>
<div class="card"><h2>Data</h2><table>{_rows([
        ('Portal readings', range_row(data.get('readings'), 'quarter hours')),
        ('Reconstructed history', range_row(data.get('reconstructed'), 'quarter hours')),
    ])}</table></div>
<div class="card"><h2>Statistics</h2><table>{_rows([
        ('Grid consumption (for the Energy dashboard)', stat_row('consumption')),
        ('Portal readings only (for checks)', stat_row('portal')),
    ])}</table></div>
<div class="card"><h2>Import older history</h2>
<p>The portal keeps only about 36 months. Upload <code>&lt;meterId&gt;_readings.csv</code> (older portal
readings, merged row by row) and <code>&lt;meterId&gt;_reconstructed_15min.csv</code> (the reconstructed
history from <code>./homebase disaggregate</code>). Files are checked and imported right away;
importing the same file again changes nothing.</p>
{pending}
<form method="post" action="upload" enctype="multipart/form-data">
<input type="file" name="files" accept=".csv" multiple required>
<button type="submit">Upload and import</button></form></div>
<div class="card"><h2>Last import</h2>{import_html}</div>
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

    def do_GET(self):
        if not self._allowed():
            return
        # /upload too: the browser stays there after an upload and the page refreshes itself
        if self.path.split('?')[0].rstrip('/') not in ('', '/index.html', '/upload'):
            self.send_error(404)
            return
        self._send(200, render_page(read_status(self.store)))

    def do_POST(self):
        if not self._allowed():
            return
        if self.path.split('?')[0].rstrip('/') != '/upload':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_UPLOAD:
            self._send(413, render_page(read_status(self.store), 'The upload is too large.'))
            return
        files = parse_upload(self.headers.get('Content-Type', ''), self.rfile.read(length))
        if not files:
            self._send(400, render_page(read_status(self.store), 'No file was uploaded.'))
            return
        names = [store_upload(self.store / 'import', name, data).name for name, data in files]
        write_status(self.store, import_pending=True)
        self.wake.set()
        logger.info('Uploaded %s, importing', ', '.join(names))
        self._send(200, render_page(read_status(self.store), f'Uploaded {", ".join(names)}.'))


def start(store: Path, wake: threading.Event, port: int = PORT) -> ThreadingHTTPServer:
    """Starts the page in a background thread."""
    handler = type('IngressHandler', (Handler,), {
        'store': store, 'wake': wake, 'allow_any': os.environ.get('ADDON_WEB_ALLOW_ANY') == '1'})
    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    threading.Thread(target=server.serve_forever, name='ingress', daemon=True).start()
    logger.info('Add-on page listening on port %d (Ingress)', port)
    return server
