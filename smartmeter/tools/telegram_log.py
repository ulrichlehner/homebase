#!/usr/bin/env python3
"""Reads the telegrams of a wmbusmeters log (add-on with `logtelegrams: true`) of the LINZ NETZ
meter and makes them safe to keep in this public repository.

wmbusmeters logs each telegram already decrypted: `telegram=|<header>_<payload>|`. The only
identifying values in it are the meter id (address field of the header) and the fabrication
number (`0C 78` record of the payload); both are replaced with the fantasy id 12345678. The key
never appears in a telegram line, and only telegram lines are read. Meter time, counters and
power stay as they are.

  telegram_log.py telegrams LOG OUT.txt.gz  every telegram, anonymised (unencrypted, one per line)
  telegram_log.py records LOG OUT.csv.gz     every telegram decoded (time, records, power)
  telegram_log.py phases LOG                 the phases of non-spec energy records, classified
  telegram_log.py samples LOG OUT.txt        anonymised telegrams around every phase switch, as
                                             unencrypted telegrams for tests/run.sh

Telegrams of other meters in range are skipped (only the most frequent id is kept).
"""

import collections
import csv
import gzip
import re
import sys
from datetime import datetime

FANTASY_ID = '78563412'           # 12345678, little endian BCD as in the telegram
TELEGRAM = re.compile(r'telegram=\|([0-9A-F]+)_([0-9A-F]+)\|')
RECORDS = re.compile(r'0C78([0-9A-F]{8})066D([0-9A-F]{12})0E(0[0-9A-F])([0-9A-F]{12})0E(8[0-9A-F])3C([0-9A-F]{12})'
                     r'0B2B([0-9A-F]{6})0BAB3C([0-9A-F]{6})')
SPEC = ('03', '83')
UNIT_WH = {'00': 0.001, '01': 0.01, '02': 0.1, '03': 1, '04': 10, '05': 100, '06': 1000, '07': 10000}


def read(path):
    """[(header, payload)] of the most frequent meter in the log."""
    with open(path, errors='replace') as fh:
        tel = [m.groups() for m in map(TELEGRAM.search, fh) if m]
    if not tel:
        return []
    own = collections.Counter(h[8:16] for h, _ in tel).most_common(1)[0][0]
    return [(h, p) for h, p in tel if h[8:16] == own]


def meter_time(hexstr):
    """CP48 date and time (type I)."""
    b = bytes.fromhex(hexstr)
    year = 2000 + (((b[3] & 0xE0) >> 5) | ((b[4] & 0xF0) >> 1))
    return datetime(year, b[4] & 0x0F, b[3] & 0x1F, b[2] & 0x1F, b[1] & 0x3F, b[0] & 0x3F)


def bcd(hexstr):
    """Little endian BCD; None if a nibble is not a digit."""
    digits = ''.join(reversed([hexstr[i:i + 2] for i in range(0, len(hexstr), 2)]))
    return int(digits) if digits.isdigit() else None


def decode(payload):
    m = RECORDS.search(payload)
    if not m:
        return None
    _, t, vi, di, ve, de, pw, pwe = m.groups()
    return {'time': meter_time(t), 'import_vif': vi, 'import_digits': bcd(di), 'export_vif': ve,
            'export_digits': bcd(de), 'power_w': bcd(pw), 'export_power_w': bcd(pwe)}


def anonymise(header, payload):
    """An unencrypted telegram with the fantasy id: the header loses its id, its encryption mode
    and the 2F2F check bytes (the payload is already decrypted), the payload its fabrication
    number. Runs through wmbusmeters with NOKEY."""
    # header: L C M(2) A(4) ver type CI(7A) acc status cfg(2) [2F2F]
    acc = header[22:24]
    body = header[2:4] + header[4:8] + FANTASY_ID + header[16:20] + '7A' + acc + '00' + '0000'
    payload = re.sub(r'^0C78[0-9A-F]{8}', '0C78' + FANTASY_ID, payload)
    data = body + payload
    return f'{len(data) // 2:02X}' + data


def phases(rows):
    """[[start, end, (import vif, export vif), count, first row]] of consecutive equal records."""
    out = []
    for r in rows:
        key = (r['import_vif'], r['export_vif'])
        if out and out[-1][2] == key:
            out[-1][1] = r['time']
            out[-1][3] += 1
        else:
            out.append([r['time'], r['time'], key, 1, r])
    return out


def classify(phase, last_spec_wh):
    """What the digits of a non-spec phase are, from the last spec counter before it."""
    digits, vif = phase[4]['import_digits'], phase[2][0]
    if not digits:
        return 'all zero'
    if last_spec_wh is None:
        return 'unknown (no spec value before)'
    near = lambda v: abs(v - last_spec_wh) <= 1000     # noqa: E731, 1 kWh: the phases last < 1 h
    if near(digits):
        return 'Wh digits, wrong VIF'
    if near(digits / 10):
        return 'tenths of Wh, wrong VIF'
    if near(digits * UNIT_WH.get(vif, 0)):
        return 'digits in the VIF unit'
    return f'other ({digits} vs counter {last_spec_wh})'


def report(rows):
    print(f'{len(rows)} telegrams from {rows[0]["time"]} to {rows[-1]["time"]} (meter time)')
    counts = collections.Counter(f'{r["import_vif"]}/{r["export_vif"]}' for r in rows)
    print('records (import/export VIF):', dict(counts.most_common()))
    last_spec, kinds = None, collections.Counter()
    print('\nnon-spec phases:')
    for p in phases(rows):
        if p[2] == SPEC:
            last_spec = [r for r in rows if p[0] <= r['time'] <= p[1]][-1]['import_digits']
            continue
        kind = classify(p, last_spec)
        kinds[(p[2][0], kind)] += 1
        print(f'  {p[0]:%Y-%m-%d %H:%M:%S}  {(p[1] - p[0]).total_seconds() / 60:5.1f} min  '
              f'VIF {p[2][0]}/{p[2][1]}  {p[3]:4d} telegrams  {kind}')
    print('\nby kind:', {f'VIF {v}: {k}': n for (v, k), n in sorted(kinds.items())})
    gaps = [(a['time'], (b['time'] - a['time']).total_seconds()) for a, b in zip(rows, rows[1:])]
    print('gaps over 2 min:', [(f'{t:%m-%d %H:%M}', round(s / 60, 1)) for t, s in gaps if s > 120])
    spec = [r for r in rows if (r['import_vif'], r['export_vif']) == SPEC]
    back = sum(1 for a, b in zip(spec, spec[1:]) if b['import_digits'] < a['import_digits'])
    print(f'spec import counter: {spec[0]["import_digits"]} -> {spec[-1]["import_digits"]} Wh, '
          f'decreases {back}; max power {max(r["power_w"] or 0 for r in rows)} W, '
          f'max export power {max(r["export_power_w"] or 0 for r in rows)} W')


def main(argv):
    cmd, log = argv[1], argv[2]
    tel = read(log)
    rows = [d for d in (decode(p) for _, p in tel) if d]
    if cmd == 'phases':
        report(rows)
    elif cmd == 'telegrams':
        with gzip.open(argv[3], 'wt') as fh:
            fh.writelines(anonymise(h, p) + '\n' for h, p in tel)
    elif cmd == 'records':
        with gzip.open(argv[3], 'wt', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['meter_time', 'import_vif', 'import_digits', 'export_vif', 'export_digits', 'power_w',
                        'export_power_w'])
            for r in rows:
                w.writerow([r['time'].isoformat(), r['import_vif'], r['import_digits'], r['export_vif'],
                            r['export_digits'], r['power_w'], r['export_power_w']])
    elif cmd == 'samples':
        # the two telegrams on each side of every switch between record kinds
        keep = set()
        for i in range(1, len(tel)):
            a, b = decode(tel[i - 1][1]), decode(tel[i][1])
            if a and b and (a['import_vif'], a['export_vif']) != (b['import_vif'], b['export_vif']):
                keep |= {i - 2, i - 1, i, i + 1}
        with open(argv[3], 'w') as fh:
            fh.write('# Real telegrams of the LINZ NETZ meter, anonymised (smartmeter/tools/telegram_log.py):\n'
                     '# fantasy id 12345678, unencrypted; meter time, counters and power as received.\n'
                     '# The two telegrams on each side of every switch between energy record kinds.\n'
                     '# name  hex  expected_import_kwh  expected_export_kwh  ("-" = no value)\n')
            for i in sorted(k for k in keep if 0 <= k < len(tel)):
                d = decode(tel[i][1])
                spec = (d['import_vif'], d['export_vif']) == SPEC
                want = (f"{d['import_digits'] / 1000} {d['export_digits'] / 1000}" if spec else "- -")
                fh.write(f'{d["time"]:%Y%m%dT%H%M%S}_vif{d["import_vif"]}  {anonymise(*tel[i])}  {want}\n')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv)
