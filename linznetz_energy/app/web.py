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

from homebase import tariffs
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
html { height: 100%; overflow: hidden; overflow: clip; }   /* clip: not even an anchor jump scrolls it */
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
.info { background: var(--muted); }
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
ul.plain { list-style: none; padding: 0; margin: 0 0 4px; }
.contract { margin-top: 18px; display: grid; gap: 12px; }
.contract h3 { margin: 0; font-size: 17px; font-weight: 500; }
.contract label { display: grid; gap: 6px; font-size: 14px; color: var(--muted); font-weight: 500; }
.contract input, .contract select { height: 44px; font: inherit; color: var(--text); background: var(--card);
                                    border: 1px solid var(--divider); border-radius: 8px; padding: 0 12px; }
.contract button { justify-self: start; width: auto; margin: 0; padding: 0 24px; height: 48px; font-size: 16px; }
.contract p { margin: 0; }
.contract [hidden] { display: none; }
.result { margin: 12px 0 0; }
.setup-steps { margin: 0 0 18px; }
.setup-steps li { line-height: 1.5; }
.price-upload { margin-top: 16px; }
.drop.compact { padding: 18px 16px; }
.drop.compact svg { width: 28px; height: 28px; }
.drop.compact strong { font-size: 16px; }
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
/* tabs: what is looked at often, the setup, data and maintenance (without JS all sections show) */
.tabs { position: sticky; top: -24px;   /* the body (the scroll container) has 24px padding */
        z-index: 2; display: flex; gap: 4px; margin: 0 -16px; padding: 8px 16px;
        background: var(--bg); border-bottom: 1px solid var(--divider); overflow-x: auto; }
.tabs a { flex: 0 0 auto; padding: 8px 14px; border-radius: 8px; color: var(--muted); text-decoration: none;
          font-weight: 500; white-space: nowrap; }
.tabs a[aria-selected="true"] { color: var(--primary); background: var(--primary-soft); }
.tabs .count { font-size: 12px; padding: 0 7px; margin-left: 2px; vertical-align: 1px; }
.tab { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; scroll-margin-top: 64px; }
.tab[hidden] { display: none; }
.card { scroll-margin-top: 64px; }
a { color: var(--primary); }
.card-head { display: flex; gap: 12px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }
.card-head h2 { margin-bottom: 8px; }
.inline-end button { margin: 0; }
.card.slim { padding: 12px 20px; }
.card.guide { border-left: 4px solid var(--primary); }
p.more { margin: 12px 0 0; font-size: 14px; }
ol.checklist { list-style: none; margin: 0; padding: 0; display: grid; gap: 0; }
ol.checklist li { display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 4px; padding: 10px 0;
                  border-bottom: 1px solid var(--divider); }
ol.checklist li:last-child { border-bottom: 0; padding-bottom: 0; }
ol.checklist .pill { justify-self: start; align-self: start; margin-top: 2px; }
ul.attention { display: grid; gap: 8px; }
/* fixed price comparison: chart (lines in SVG, labels in the page's type) and the table of start months */
.chart { margin: 18px 0 6px; font-size: 14px; color: var(--muted); }
.chart .plot { position: relative; height: 180px; margin: 8px 0 0 32px; }
.chart svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.chart .grid line { stroke: var(--divider); stroke-width: 1; }
.chart polyline { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
.chart .actual { stroke: var(--primary); }
.chart .fixed { stroke: var(--warn); stroke-dasharray: 6 4; }
.chart .ylabels span { position: absolute; left: -32px; width: 24px; text-align: right; transform: translateY(-50%);
                       font-size: 12px; line-height: 1; }
.chart .xlabels { position: relative; height: 18px; margin: 6px 0 0 32px; font-size: 12px; }
.chart .xlabels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.chart .xlabels .first { transform: none; }
.chart .xlabels .last { transform: translateX(-100%); }
.chart figcaption { display: flex; gap: 4px 16px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
.chart figcaption > span { white-space: nowrap; }
.chart figcaption > span:last-child { margin-left: auto; }
.key { display: inline-block; width: 20px; height: 0; margin-right: 4px; vertical-align: middle; border-top: 2.5px solid; }
.key.actual { border-color: var(--primary); }
.key.fixed { border-color: var(--warn); border-top-style: dashed; }
.scroll { overflow-x: auto; margin-top: 12px; }
table.grid th { width: auto; font-size: 14px; }
table.grid td { padding-right: 16px; white-space: nowrap; }
table.grid th { padding-right: 16px; vertical-align: bottom; overflow-wrap: normal; }
td.more { color: var(--err); }
td.less { color: var(--ok); }
.note { font-size: 14px; margin: 10px 0 0; }
/* phones: less padding, label above value instead of two table columns */
@media (max-width: 600px) {
  body { padding: 16px 12px; font-size: 15px; }
  main, .tab { gap: 14px; }
  .tabs { top: -16px; margin: 0 -12px; padding: 6px 12px; }
  .tabs a { padding: 8px 10px; }
  .card { padding: 16px; border-radius: 10px; }
  h1 { font-size: 24px; }
  h2 { font-size: 18px; margin-bottom: 10px; }
  table:not(.grid), table:not(.grid) tbody, table:not(.grid) tr, table:not(.grid) th, table:not(.grid) td { display: block; width: 100%; }
  table:not(.grid) tr { padding: 10px 0; border-bottom: 1px solid var(--divider); }
  table:not(.grid) tr:last-child { border-bottom: 0; padding-bottom: 0; }
  table:not(.grid) th, table:not(.grid) td { border: 0; padding: 0; }
  table:not(.grid) th { font-size: 14px; margin-bottom: 2px; }
  .drop { padding: 22px 12px; }
  .drop strong { font-size: 17px; }
  button { padding: 14px 16px; }
  .pick .row { flex-direction: column; }
  .pick select, .pick button { flex: 0 0 48px; width: 100%; }
  table.grid td, table.grid th { padding-right: 8px; }
  table.grid td:last-child, table.grid th:last-child { padding-right: 0; }
  table.grid .per { display: block; }
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


def _eur(value: float) -> str:
    return f'{value:,.2f}'.replace(',', ' ').replace('.', ',') if value is not None else ''


def _month_ranges(months: list) -> str:
    """'2023-03', '2023-04', '2023-06' -> '03/2023 – 04/2023, 06/2023'."""
    def index(m):
        y, mo = m.split('-')
        return int(y) * 12 + int(mo) - 1
    fmt = lambda i: f'{i % 12 + 1:02d}/{i // 12}'   # noqa: E731
    out, run = [], []
    for i in sorted(index(m) for m in months):
        if run and i != run[-1] + 1:
            out.append(run)
            run = []
        run.append(i)
    if run:
        out.append(run)
    return ', '.join(fmt(r[0]) if len(r) == 1 else f'{fmt(r[0])} – {fmt(r[-1])}' for r in out)


def _costs_cards(lang: str, status: dict) -> tuple:
    """(Costs card for the overview: grid operator, supplier contract, price now, per year, months
    without price; Supplier card for the setup: the contract form and the price list upload)."""
    c = status.get('costs') or {}
    contract = c.get('contract')
    grid = html.escape(c['grid']) if c.get('grid') else f'<span class="muted">{html.escape(t(lang, "page.costs_grid_unknown"))}</span>'
    g = c.get('grid_status') or {}
    if g.get('state') == 'online':
        grid += f'<br><span class="muted">{html.escape(t(lang, "page.costs_grid_online"))}</span>'
    elif g.get('state') == 'table':
        grid += f'<br><span class="pill info">i</span> {html.escape(t(lang, "page.costs_grid_table", error=g.get("error") or "–"))}'
    elif g.get('state') == 'outdated':
        grid += f'<br><span class="pill warn">!</span> {html.escape(t(lang, "page.costs_grid_outdated", year=g.get("year", "")))}'
    rows = [(t(lang, 'page.costs_grid'), grid)]
    rows.append((t(lang, 'page.costs_supplier'),
                 f'<strong>{html.escape(contract["supplier"])}</strong><br>'
                 f'{html.escape(t(lang, "page.costs_since", product=contract["product"] or "", start=contract["start"]))}'
                 if contract else f'<span class="muted">{_rich(t(lang, "page.costs_no_contract"))}</span>'))
    if contract:
        price = (html.escape(t(lang, 'page.costs_price_line', gross=_eur(c['kwh_ct_gross']), energy=_eur(c['energy_ct'])))
                 if c.get('energy_ct') is not None else f'<span class="muted">{html.escape(t(lang, "page.costs_price_none"))}</span>')
        if (c.get('fetch_error') or '').startswith('not published yet'):
            price += f'<br><span class="pill info">i</span> {html.escape(t(lang, "page.costs_price_pending"))}'
        elif c.get('fetch_error'):
            price += f'<br><span class="pill warn">!</span> {html.escape(t(lang, "page.costs_fetch_error", error=c["fetch_error"]))}'
        rows.append((t(lang, 'page.costs_price'), price))
    if c.get('years') and 'last12' in c:
        years = ''.join(f'<li>{html.escape(y)}: {_eur(v)} €</li>' for y, v in c['years'].items())
        rows.append((t(lang, 'page.costs_years'), f'<ul class="plain">{years}</ul>'
                     f'<span class="muted">{html.escape(t(lang, "page.costs_last12", eur=_eur(c["last12"])))}</span>'))
    if c.get('unpriced'):
        rows.append((t(lang, 'page.costs_unpriced'), f'<span class="pill info">{len(c["unpriced"])}</span> '
                     f'{html.escape(_month_ranges(c["unpriced"]))}<br><span class="muted">'
                     f'{html.escape(t(lang, "page.costs_unpriced_hint"))}</span>'))
    if c.get('from') and c.get('id'):
        rows.append((t(lang, 'page.costs_statistic'), f'<code>{html.escape(c["id"])}</code><br>'
                     f'{html.escape(t(lang, "page.costs_statistic_line", eur=_eur(c.get("eur", 0.0)), first=c["from"]))}'))
    result = (status.get('contract_result') or {}).get('state')
    result_html = ({'pending': f'<p class="result"><span class="pill run">…</span> {html.escape(t(lang, "page.contract_calculating"))}</p>',
                    'calculating': f'<p class="result"><span class="pill run">…</span> {html.escape(t(lang, "page.contract_calculating"))}</p>',
                    'saved': f'<p class="result"><span class="pill ok">ok</span> {html.escape(t(lang, "page.contract_saved"))}</p>',
                    'invalid': f'<p class="result"><span class="pill warn">!</span> {html.escape(t(lang, "page.contract_invalid"))}</p>'}
                   .get(result, ''))
    pi = status.get('prices_import') or {}
    import_html = ''
    if pi.get('state') == 'importing':
        import_html = f'<p class="result"><span class="pill run">…</span> {html.escape(t(lang, "page.prices_importing"))}</p>'
    elif pi.get('lines'):
        import_html = ''.join(f'<p class="result"><span class="pill {"ok" if k == "import.prices" else "warn"}">'
                              f'{"ok" if k == "import.prices" else "!"}</span> {html.escape(_text(lang, [k, v]))}</p>'
                              for k, v in pi['lines'])
    # one action: choosing or dropping a file uploads it right away (no second button)
    upload = (f'<form method="post" action="upload?to=costs" enctype="multipart/form-data" class="price-upload" id="price-upload">'
              f'<label class="drop compact" id="price-drop">'
              f'<input type="file" name="files" accept=".csv" id="price-file">'
              f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>'
              f'<strong id="price-title">{t(lang, "page.prices_upload")}</strong>'
              f'<span class="muted">{html.escape(t(lang, "page.prices_upload_hint"))}</span></label>{import_html}</form>'
              '<script>(function(){var f=document.getElementById("price-upload"),i=document.getElementById("price-file"),'
              'd=document.getElementById("price-drop"),t=document.getElementById("price-title");'
              f'function send(){{if(!i.files.length)return;t.textContent={json.dumps(t(lang, "page.prices_uploading"))};'
              'd.classList.add("over");f.submit();}'
              'i.addEventListener("change",send);'
              '["dragenter","dragover"].forEach(function(e){d.addEventListener(e,function(v){v.preventDefault();d.classList.add("over");});});'
              '["dragleave"].forEach(function(e){d.addEventListener(e,function(v){v.preventDefault();d.classList.remove("over");});});'
              'd.addEventListener("drop",function(v){v.preventDefault();i.files=v.dataTransfer.files;send();});})();</script>')
    current = contract or {}
    params = current.get('params', {})
    suppliers = ''.join(f'<option value="{s.id}"{" selected" if s.id == current.get("supplier_id") else ""}>'
                        f'{html.escape(s.name)}</option>' for s in tariffs.SUPPLIERS.values())
    products = ''.join(f'<option value="{html.escape(p)}"{" selected" if p == current.get("product") else ""}>'
                       f'{html.escape(p)}</option>' for p in tariffs.SUPPLIERS['gogreen_flex'].products)
    flex, fixed = tariffs.SUPPLIERS['gogreen_flex'].defaults, tariffs.SUPPLIERS['fixed'].defaults

    def field(name, label, value, only):
        return (f'<label data-only="{only}">{html.escape(t(lang, label))}'
                f'<input name="{name}" inputmode="decimal" value="{html.escape(str(value))}"></label>')
    form = (f'<form method="post" action="contract" class="contract" id="contract">'
            f'<h3>{t(lang, "page.contract")}</h3>'
            f'<label>{t(lang, "page.contract_supplier")}<select name="supplier" id="supplier">{suppliers}</select></label>'
            f'<label data-only="gogreen_flex">{t(lang, "page.contract_product")}<select name="product">{products}</select></label>'
            f'<label>{t(lang, "page.contract_start")}<input type="date" name="start" required '
            f'value="{html.escape(current.get("start_iso", ""))}"></label>'
            + field('energy_ct_kwh', 'page.contract_energy', params.get('energy_ct_kwh', fixed['energy_ct_kwh']), 'fixed')
            + field('base_fee_month', 'page.contract_base_fee', params.get('base_fee_month', flex['base_fee_month']), 'any')
            + field('online_bonus_month', 'page.contract_online_bonus',
                    round(params.get('online_bonus_month', flex['online_bonus_month']), 6), 'gogreen_flex')
            + f'<p class="muted">{html.escape(t(lang, "page.contract_hint"))}</p>'
            f'<button type="submit">{t(lang, "page.contract_save")}</button>{result_html}</form>'
            '<script>(function(){var s=document.getElementById("supplier");function show(){'
            'document.querySelectorAll(".contract [data-only]").forEach(function(l){var o=l.getAttribute("data-only");'
            'var on=o==="any"||o===s.value;l.hidden=!on;l.querySelectorAll("input,select").forEach(function(i){i.disabled=!on;});});}'
            's.addEventListener("change",show);show();})();</script>')
    summary = (f'<div class="card" id="costs"><h2>{t(lang, "page.costs")}</h2>'
               f'<p class="lead">{html.escape(t(lang, "page.costs_lead"))}</p><table>{_rows(rows)}</table></div>')
    setup = (f'<div class="card" id="supplier"><h2>{t(lang, "page.supplier_card")}</h2>'
             f'<p class="lead">{html.escape(t(lang, "page.supplier_lead"))}</p>{form}{upload}</div>')
    return summary, setup


def _month(value: str) -> str:
    """'2026-03' -> '03/2026'."""
    y, m = value.split('-')
    return f'{m}/{y}'


def _date(value: Optional[str]) -> str:
    """'2027-12-31' -> '31.12.2027'."""
    return '.'.join(reversed(value.split('-'))) if value else ''


def _signed(value: float) -> str:
    return ('+' if value > 0 else '−' if value < 0 else '') + _eur(abs(value))


def _price_chart(lang: str, months: list) -> str:
    """Line chart: the energy price actually paid per month against go green's fixed offer of
    that month, ct/kWh gross. The SVG only draws the lines (stretched to the plot, strokes keep
    their width); axis labels and legend are HTML in the page's own type, so they read the
    same on a phone as on a desktop."""
    values = [v for m in months for v in (m['actual_ct'], m['fixed_ct']) if v is not None]
    if len(months) < 2 or not values:
        return ''
    step = 5 if max(values) - min(values) > 15 else 2
    lo, hi = (min(values) // step) * step, (max(values) // step + 1) * step   # a line chart needs no zero
    x = lambda i: 100 * i / (len(months) - 1)          # noqa: E731   percent of the plot width
    y = lambda v: 100 * (1 - (v - lo) / (hi - lo))     # noqa: E731   percent of the plot height

    def line(key, cls):
        points = ' '.join(f'{x(i):.2f},{y(m[key]):.2f}' for i, m in enumerate(months) if m[key] is not None)
        return f'<polyline class="{cls}" points="{points}" vector-effect="non-scaling-stroke"/>'
    ticks = range(int(lo), int(hi) + 1, int(step))
    grid = ''.join(f'<line x1="0" x2="100" y1="{y(v):.2f}" y2="{y(v):.2f}" vector-effect="non-scaling-stroke"/>' for v in ticks)
    ylabels = ''.join(f'<span style="top:{y(v):.2f}%">{v}</span>' for v in ticks)
    every = -(-len(months) // 4)                       # about four month labels (fit a phone), the last one always
    last = len(months) - 1
    xlabels = ''.join(f'<span class="{"first" if i == 0 else "last" if i == last else ""}" style="left:{x(i):.2f}%">'
                      f'{_month(m["month"])}</span>'
                      for i, m in enumerate(months) if i == last or (i % every == 0 and last - i >= every))
    return (f'<figure class="chart" role="img" aria-label="{html.escape(t(lang, "page.compare_chart"))}">'
            f'<div class="plot"><div class="ylabels">{ylabels}</div>'
            f'<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><g class="grid">{grid}</g>'
            f'{line("fixed_ct", "fixed")}{line("actual_ct", "actual")}</svg></div>'
            f'<div class="xlabels">{xlabels}</div>'
            f'<figcaption><span><span class="key actual"></span> {html.escape(t(lang, "page.compare_actual"))}</span>'
            f'<span><span class="key fixed"></span> {html.escape(t(lang, "page.compare_fixed"))}</span>'
            f'<span>ct/kWh</span></figcaption></figure>')


def _comparison_card(lang: str, status: dict) -> str:
    """The fixed price comparison (homebase.compare): today's offer against the last 12 months,
    the prices per month, and per start month what a switch then would have meant until now."""
    c = status.get('comparison')
    if not c or not c.get('rows'):
        return ''
    product = c.get('product') or 'strom flex'
    fixed_product = product.replace('flex', '').replace('  ', ' ').strip()
    rows = []
    today = c.get('today')
    if today:
        verdict = 'page.compare_today_more' if today['eur'] > 0 else 'page.compare_today_less'
        rows.append((t(lang, 'page.compare_offer'),
                     html.escape(t(lang, 'page.compare_offer_line', product=fixed_product, ct=_eur(today['ct']),
                                   fee=_eur(today['fee']), until=_date(today['until']) or '?'))))
        rows.append((t(lang, 'page.compare_last12', months=today['months']),
                     html.escape(t(lang, 'page.compare_last12_line', actual=_eur(today['actual_ct']),
                                   fixed=_eur(today['fixed_ct']), kwh=f'{today["kwh"]:,.0f}'.replace(',', '\u00a0')))
                     + f'<br><strong>{html.escape(t(lang, verdict, eur=_eur(abs(today["eur"]))))}</strong>'
                     + f'<br><span class="muted">{html.escape(t(lang, "page.compare_per_ct", eur=_eur(today["eur_per_ct_year"])))}</span>'))
    head = (f'<tr><th>{t(lang, "page.compare_start")}</th><th>{t(lang, "page.compare_price")}</th>'
            f'<th>{html.escape(t(lang, "page.compare_diff", through=_month(c["through"])))}</th>'
            f'<th>{t(lang, "page.compare_until")}</th></tr>')
    body = ''
    for r in reversed(c['rows']):
        per_kwh = f' <span class="muted per">({_signed(r["ct_kwh"])} ct/kWh)</span>' if r['ct_kwh'] is not None else ''
        body += (f'<tr><td>{_month(r["month"])}</td><td>{_eur(r["ct"])}</td>'
                 f'<td class="{"more" if r["eur"] > 0 else "less"}">{_signed(r["eur"])} €{per_kwh}</td>'
                 f'<td>{_date(r["until"]) or "?"}{"*" if r["assumed"] else ""}</td></tr>')
    notes = [t(lang, 'page.compare_how')]
    if any(r['assumed'] for r in c['rows']):
        notes.append(t(lang, 'page.compare_assumed'))
    if c.get('offer_error'):
        notes.append(t(lang, 'page.compare_offer_error', error=c['offer_error']))
    return (f'<div class="card" id="comparison"><h2>{t(lang, "page.compare")}</h2>'
            f'<p class="lead">{html.escape(t(lang, "page.compare_lead", product=fixed_product))}</p>'
            f'{f"<table>{_rows(rows)}</table>" if rows else ""}'
            f'{_price_chart(lang, c.get("months") or [])}'
            f'<div class="scroll"><table class="grid">{head}{body}</table></div>'
            + ''.join(f'<p class="muted note">{html.escape(n)}</p>' for n in notes) + '</div>')


# -- Setup checklist and what needs attention --------------------------------------------------

REQUIRED_STEPS = ('portal', 'download', 'supplier', 'dashboard')


def setup_steps(status: dict) -> list:
    """[(step, state, values)] of the setup in order; state: 'done', 'run' (in progress),
    'todo' (required, open), 'optional' (open, can be left), 'later' (waits for an earlier step),
    'problem'. The page and its tests use these; the texts are page.check_<step>_<state>."""
    portal, data, costs = status.get('portal') or {}, status.get('data') or {}, status.get('costs') or {}
    energy, phase = status.get('energy') or {}, status.get('phase')
    readings = data.get('readings')
    out = []
    if status.get('setup'):
        out.append(('portal', 'todo', {}))
    elif portal.get('login_failed') and not portal.get('ok'):
        out.append(('portal', 'problem', {}))
    elif portal.get('ok'):
        out.append(('portal', 'done', {}))
    else:
        out.append(('portal', 'run', {}))
    if readings:
        out.append(('download', 'done', {'count': f'{readings["count"]:,}'.replace(',', '\u00a0'), 'first': readings['from']}))
    else:
        out.append(('download', 'run' if phase in ('downloading', 'starting', 'importing') else 'later', {}))
    gaps = len(((status.get('statistics') or {}).get('consumption') or {}).get('gaps') or [])
    out.append(('history', 'done' if data.get('reconstructed') or (readings and not gaps) else
                'optional' if readings else 'later', {'count': gaps}))
    out.append(('live', 'done' if status.get('live_meter') else 'optional', {'entity': status.get('live_meter') or ''}))
    contract = costs.get('contract')
    out.append(('supplier', 'done' if contract else 'todo',
                {'supplier': contract['supplier'], 'start': contract['start']} if contract else {}))
    unpriced = costs.get('unpriced') or []
    out.append(('prices', 'optional' if unpriced else 'done' if contract and costs.get('from') else 'later',
                {'count': len(unpriced)}))
    if energy.get('uses_consumption') and (energy.get('uses_cost') or not costs.get('from')):
        out.append(('dashboard', 'done', {}))
    else:
        out.append(('dashboard', 'todo' if readings else 'later', {}))
    return out


# where each step is done: the card it links to
STEP_TARGETS = {'portal': 'config', 'download': 'status', 'history': 'history', 'live': 'live',
                'supplier': 'supplier', 'prices': 'price-upload', 'dashboard': 'energy'}


def open_required(steps: list) -> int:
    return sum(1 for step, state, _ in steps if step in REQUIRED_STEPS and state not in ('done',))


def _checklist_card(lang: str, steps: list, guiding: bool) -> str:
    marks = {'done': ('ok', '✓'), 'run': ('run', '…'), 'todo': ('run', '→'), 'optional': ('info', '○'),
             'later': ('info', '○'), 'problem': ('err', '!')}
    items = ''
    for step, state, values in steps:
        cls, mark = marks[state]
        optional = '' if step in REQUIRED_STEPS else f' <span class="muted">{t(lang, "page.check_optional")}</span>'
        detail = _rich(t(lang, f'page.check_{step}_{state}', **values))
        link = (f' <a href="#{STEP_TARGETS[step]}">{t(lang, "page.check_go")}</a>'
                if state in ('todo', 'optional', 'problem') else '')
        items += (f'<li class="{state}"><span class="pill {cls}">{mark}</span><div><strong>{t(lang, f"page.check_{step}")}</strong>'
                  f'{optional}<br><span class="muted">{detail}</span>{link}</div></li>')
    left = open_required(steps)
    lead = (t(lang, 'page.checklist_lead_open', count=left) if left else t(lang, 'page.checklist_lead_done'))
    skip = (f'<form method="post" action="onboarding" class="inline-end"><input type="hidden" name="guide" value="skip">'
            f'<button type="submit" class="secondary small">{t(lang, "page.checklist_skip")}</button></form>'
            if guiding else '')
    title = t(lang, 'page.checklist_guide' if guiding else 'page.checklist')
    return (f'<div class="card{" guide" if guiding else ""}" id="checklist"><div class="card-head"><h2>{title}</h2>{skip}</div>'
            f'<p class="lead">{html.escape(lead)}</p><ol class="checklist">{items}</ol></div>')


def attention(status: dict) -> list:
    """[(level, text key, values, card id)]: what needs a look, for the overview."""
    out = []
    portal, costs = status.get('portal') or {}, status.get('costs') or {}
    if portal.get('login_failed'):
        out.append(('err', 'page.attn_login', {}, 'status'))
    stat = (status.get('statistics') or {}).get('consumption') or {}
    if stat.get('gaps'):
        out.append(('warn', 'page.attn_gaps', {'count': len(stat['gaps'])}, 'statistics'))
    check = status.get('live_check') or {}
    if check.get('bad_days'):
        out.append(('warn', 'page.attn_deviation', {'count': len(check['bad_days'])}, 'ranges'))
    if (costs.get('grid_status') or {}).get('state') == 'outdated':
        out.append(('warn', 'page.attn_grid', {'year': costs['grid_status'].get('year', '')}, 'costs'))
    if costs.get('fetch_error') and not costs['fetch_error'].startswith('not published yet'):
        out.append(('warn', 'page.attn_price', {}, 'costs'))
    energy = status.get('energy') or {}
    if energy.get('uses_consumption') and len(energy.get('grid_from') or []) > 1:
        out.append(('warn', 'page.attn_double', {}, 'energy'))
    if costs.get('unpriced'):
        out.append(('info', 'page.attn_unpriced', {'count': len(costs['unpriced'])}, 'price-upload'))
    return out


def _attention_card(lang: str, items: list) -> str:
    if not items:
        return ''
    marks = {'err': '!', 'warn': '!', 'info': 'i'}
    rows = ''.join(f'<li><span class="pill {level}">{marks[level]}</span> {html.escape(t(lang, key, **values))} '
                   f'<a href="#{target}">{t(lang, "page.check_go")}</a></li>' for level, key, values, target in items)
    return f'<div class="card" id="attention"><h2>{t(lang, "page.attention")}</h2><ul class="plain attention">{rows}</ul></div>'


TABS = ('overview', 'setup', 'data')


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
            or status.get('live_meter_request') or status.get('fetch_request')
            or status.get('contract_request')
            or (status.get('contract_result') or {}).get('state') in ('pending', 'calculating')
            or (status.get('prices_import') or {}).get('state') == 'importing')
    refresh = '<meta http-equiv="refresh" content="5">' if busy else ''

    setup = status.get('setup') or []
    setup_html = ''
    if setup:
        items = ''.join(f'<li>{html.escape(_text(lang, line))}</li>' for line in setup)
        setup_html = (f'<div class="card setup" id="config"><h2>{t(lang, "page.setup_title")}</h2><ul>{items}</ul>'
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
        estimated = (f'<br><span class="pill info">{t(lang, "page.estimated", count=s["estimated"])}</span> '
                     f'<span class="muted">{html.escape(t(lang, "page.estimated_hint"))}</span>'
                     if s.get('estimated') else '')
        line = t(lang, 'page.stat_line', hours=s['hours'], first=s['from'], last=s['to'], kwh=f'{s["kwh"]:.1f}')
        return (f'<code>{html.escape(s["id"])}</code><br>{html.escape(line)}<br>'
                f'<span class="muted">{html.escape(sources)}</span>{flagged}{estimated}')

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
        remove_html = (f'<div class="card danger" id="remove"><h2>{t(lang, "page.removed_title")}</h2>'
                       f'<p>{html.escape(t(lang, "page.removed_text"))}</p>'
                       f'<form method="post" action="resume"><button type="submit" class="secondary">'
                       f'{t(lang, "page.resume")}</button></form></div>')
    else:
        problem = {'refused': t(lang, 'page.remove_refused'),
                   'failed': t(lang, 'page.remove_failed', error=removal.get('error', ''))}.get(removal.get('state'))
        refused = f'<p><span class="pill warn">!</span> {html.escape(problem)}</p>' if problem else ''
        remove_html = (f'<div class="card danger" id="remove"><h2>{t(lang, "page.remove_title")}</h2>'
                       f'<p>{html.escape(t(lang, "page.remove_text"))}</p>{refused}'
                       f'<form method="post" action="remove" onsubmit="return confirm({html.escape(json.dumps(t(lang, "page.remove_confirm")))})">'
                       f'<label class="check"><input type="checkbox" name="confirm" value="yes" required> '
                       f'{html.escape(t(lang, "page.remove_check"))}</label>'
                       f'<button type="submit" class="danger">{t(lang, "page.remove_button")}</button></form></div>')
    costs_html, supplier_html = (_costs_cards(lang, status) if 'costs' in status or 'statistics' in status
                                 else ('', ''))
    comparison_html = _comparison_card(lang, status)

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
        power_row = []
        if current and 'live_power' in status:
            power_row = [(t(lang, 'page.live_power'),
                          f'<code>{html.escape(status["live_power"])}</code><br><span class="muted">'
                          f'{html.escape(t(lang, "page.live_power_hint"))}</span>' if status.get('live_power') else
                          f'<span class="muted">{html.escape(t(lang, "page.live_power_none"))}</span>')]
        result = status.get('live_meter_result') or {}
        result_text = {'saved': lambda: t(lang, 'page.live_meter_saved', entity=result.get('entity') or t(lang, 'page.live_meter_none')),
                       'unknown': lambda: t(lang, 'page.live_meter_unknown', entity=result.get('entity', ''))}.get(result.get('state'))
        result_html = (f'<p><span class="pill {"ok" if result.get("state") == "saved" else "warn"}">'
                       f'{"ok" if result.get("state") == "saved" else "!"}</span> {html.escape(result_text())}</p>'
                       if result_text else '')
        empty = f'<p class="muted">{html.escape(t(lang, "page.live_meter_no_candidates"))}</p>' if not candidates else ''
        live_meter_html = (f'<div class="card" id="live"><h2>{t(lang, "page.live_meter")}</h2>'
                           f'<p class="lead">{html.escape(t(lang, "page.live_meter_lead"))}</p>'
                           f'<table>{_rows([(t(lang, "page.live_meter_in_use"), in_use)] + power_row)}</table>{empty}{result_html}'
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
                    'page.energy_step_pick' if current else 'page.energy_step_add',
                    'page.energy_step_cost' if (status.get('costs') or {}).get('from') else 'page.energy_step_save')
            cost = t(lang, 'stat.cost', site=status.get('site', ''))
            steps = ''.join(f'<li>{_rich(t(lang, k, name=name, current=current, cost=cost))}</li>' for k in keys)
            body = (f'<table>{_rows([(t(lang, "page.energy_uses"), used)])}</table>'
                    f'<p class="lead">{_rich(t(lang, "page.energy_switch", name=name))}</p>'
                    f'<ol class="steps">{steps}</ol>'
                    f'<p class="muted">{html.escape(t(lang, "page.energy_undo"))}</p>')
        energy_html = f'<div class="card" id="energy"><h2>{t(lang, "page.energy")}</h2>{body}</div>'

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

    # three tabs: what is looked at often, the setup, data and maintenance; the guide opens the
    # setup while a required step is open (until it is skipped)
    steps = setup_steps(status)
    left = open_required(steps)
    removed = removal.get('state') == 'done' or phase == 'removed'
    guiding = bool(left) and not status.get('guide_skipped') and not removed
    start_tab = 'setup' if guiding else 'overview'
    setup_line = (f'<div class="card slim"><span class="pill run">→</span> {html.escape(t(lang, "page.setup_open", count=left))} '
                  f'<a href="#checklist">{t(lang, "page.check_go")}</a></div>' if left and not guiding and not removed else '')
    live_line = check_row() if status.get('live_check') else ''
    summary_rows = [(t(lang, 'page.state'), f'<span class="pill {pill_class}">{html.escape(pill_text)}</span> {html.escape(detail)}'),
                    (t(lang, 'page.last_run'), _fmt_time(status.get('last_run'), lang)),
                    (t(lang, 'page.portal_login'), login)]
    if live_line:
        summary_rows.append((t(lang, 'page.live_check'), live_line))
    summary_html = (f'<div class="card" id="summary"><h2>{t(lang, "page.status")}</h2><table>{_rows(summary_rows)}</table>'
                    f'<p class="more"><a href="#status">{t(lang, "page.status_details")}</a></p></div>')
    tab_labels = {'overview': t(lang, 'page.tab_overview'),
                  'setup': t(lang, 'page.tab_setup') + (f' <span class="pill run count">{left}</span>' if left else ''),
                  'data': t(lang, 'page.tab_data')}
    nav = ''.join(f'<a href="#{k}" data-tab="{k}" role="tab">{v}</a>' for k, v in tab_labels.items())
    script = """<script>
(function () {
  var tabs = %s, start = %s, key = 'linznetz-tab';
  var links = document.querySelectorAll('.tabs a'), panes = {};
  tabs.forEach(function (k) { panes[k] = document.getElementById(k); });
  function tabOf(id) {
    if (tabs.indexOf(id) >= 0) return id;
    var el = id && document.getElementById(id);
    for (var k in panes) { if (el && panes[k].contains(el)) return k; }
    return null;
  }
  function show(k, el) {
    tabs.forEach(function (n) { panes[n].hidden = n !== k; });
    links.forEach(function (a) { a.setAttribute('aria-selected', a.dataset.tab === k ? 'true' : 'false'); });
    try { sessionStorage.setItem(key, k); } catch (e) {}
    // only the body scrolls (see STYLE): an anchor jump or scrollIntoView would also move html,
    // and with it the sticky tab bar out of sight
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = el ? document.body.scrollTop + el.getBoundingClientRect().top - 72 : 0;
  }
  function route() {
    var id = decodeURIComponent(location.hash.slice(1)), k = tabOf(id), stored = null;
    try { stored = sessionStorage.getItem(key); } catch (e) {}
    if (k) { show(k, tabs.indexOf(id) < 0 ? document.getElementById(id) : null); }
    else { show(tabOf(stored) || start, null); }
  }
  document.documentElement.classList.add('js');
  window.addEventListener('hashchange', route);
  window.addEventListener('load', route);   // after the browser's own jump to the anchor
  route();
})();
</script>""" % (json.dumps(list(TABS)), json.dumps(start_tab))
    top = f'{note}{setup_html}{remove_html if removed else ""}'
    overview = f'{setup_line}{_attention_card(lang, attention(status))}{costs_html}{comparison_html}{summary_html}'
    setup = f'{_checklist_card(lang, steps, guiding)}{live_meter_html}{supplier_html}{energy_html}'
    history = f"""<div class="card" id="history"><h2>{t(lang, 'page.import_title')}</h2>
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
</div>"""
    data_rows = [(t(lang, 'page.readings'), range_row(data.get('readings'))),
                 (t(lang, 'page.reconstructed'), range_row(data.get('reconstructed')))]
    if 'live' in data and (data.get('live') or status.get('live_check')):
        data_rows += [(t(lang, 'page.live'), live_row(data.get('live'))), (t(lang, 'page.live_check'), check_row())]
    stat_rows = [(t(lang, 'page.stat_consumption'), stat_row('consumption'))]
    if stats.get('consumption'):
        stat_rows.append((t(lang, 'page.gaps'), gaps_row('consumption')))
    maintenance = (f'<div class="card" id="status"><h2>{t(lang, "page.status")}</h2><table>{_rows(status_rows)}</table></div>'
                   f'<div class="card" id="ranges"><h2>{t(lang, "page.data")}</h2><table>{_rows(data_rows)}</table></div>'
                   f'<div class="card" id="statistics"><h2>{t(lang, "page.statistics")}</h2><table>{_rows(stat_rows)}</table></div>'
                   f'{history}'
                   f'<div class="card" id="last-import"><h2>{t(lang, "page.last_import")}</h2>{import_html}</div>'
                   f'{"" if removed else remove_html}')
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>LINZ NETZ Energy</title><style>{STYLE}</style></head>
<body><main>
<div><h1>LINZ NETZ Energy</h1><p class="muted">{site_line}</p></div>
{top}
<nav class="tabs" role="tablist">{nav}</nav>
<section class="tab" id="overview" aria-label="{html.escape(t(lang, 'page.tab_overview'))}">{overview}</section>
<section class="tab" id="setup" aria-label="{html.escape(t(lang, 'page.tab_setup'))}">{setup}</section>
<section class="tab" id="data" aria-label="{html.escape(t(lang, 'page.tab_data'))}">{maintenance}</section>
{script}
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

    def _done(self, message: Optional[tuple], anchor: str = '') -> None:
        """Answers a form with a redirect to the page (303, relative, so it works under any Ingress
        path): the browser shows the page itself, its refresh and reload do not repeat the action.
        The message is shown once on top of the page; actions of a card (`anchor`) show their
        state in that card instead and return to it."""
        if message:
            write_status(self.store, notice=[message[0], message[1]])
        self.send_response(303)
        self.send_header('Location', './' + (f'#{anchor}' if anchor else ''))
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
        to_costs = 'to=costs' in self.path.split('?', 1)[-1]
        if path == '/upload':
            if length > MAX_UPLOAD:
                self._done(('page.too_large', {}))
                return
            files = parse_upload(self.headers.get('Content-Type', ''), self.rfile.read(length))
            if not files:
                self._done(('page.no_file', {}))
                return
            names = [store_upload(self.store / 'import', name, data).name for name, data in files]
            write_status(self.store, import_pending=True, **({'prices_import': {'state': 'importing'}} if to_costs else {}))
            self.wake.set()
            logger.info('Uploaded %s, importing', ', '.join(names))
            if to_costs:
                self._done(None, 'price-upload')
            else:
                self._done(('page.uploaded', {'files': ', '.join(names)}), 'history')
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
            self._done((f'page.{path.strip("/")}_requested', {}), 'remove' if path == '/remove' else '')
        elif path == '/fetch':
            write_status(self.store, fetch_request=True)
            self.wake.set()
            logger.info('Requested on the add-on page: portal fetch')
            self._done(('page.fetch_requested', {}), 'status')
        elif path == '/contract':
            fields = {k: v[0].strip() for k, v in parse_qs(body).items() if v}
            write_status(self.store, contract_request=fields, contract_result={'state': 'pending'})
            self.wake.set()
            self._done(None, 'contract')
        elif path == '/onboarding':
            # the page alone keeps this (nothing for the main loop to do)
            skipped = (parse_qs(body).get('guide') or [''])[0] == 'skip'
            write_status(self.store, guide_skipped=skipped)
            self._done(None, 'overview' if skipped else 'checklist')
        elif path == '/live_meter':
            entity = (parse_qs(body).get('entity') or [''])[0].strip()
            if entity and not re.fullmatch(r'sensor\.[a-z0-9_]+', entity):
                self._done(('page.live_meter_unknown', {'entity': entity}))
                return
            write_status(self.store, live_meter_request={'entity': entity}, live_meter_result=None)
            self.wake.set()
            self._done(('page.live_meter_requested', {}), 'live')


ACTIONS = ('/upload', '/fetch', '/live_meter', '/contract', '/remove', '/resume', '/onboarding')


def start(store: Path, wake: threading.Event, port: int = PORT) -> ThreadingHTTPServer:
    """Starts the page in a background thread."""
    handler = type('IngressHandler', (Handler,), {
        'store': store, 'wake': wake, 'allow_any': os.environ.get('ADDON_WEB_ALLOW_ANY') == '1'})
    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    threading.Thread(target=server.serve_forever, name='ingress', daemon=True).start()
    logger.info('Add-on page listening on port %d (Ingress)', port)
    return server
