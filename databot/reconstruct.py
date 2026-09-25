#!/usr/bin/env python3
"""Reconstructs 6-hour consumption sums from the archived weekly chart PNGs.

The original InfluxDB with quarter-hour readings before 2023-09-01 was lost, but
the weekly charts rendered from it survive. Every chart prints the four 6-hour
sums of each day (as rotated labels above the bars) and the daily total, and
every week appears in up to three charts (as "current", "last" and "week before
last"). This script

  1. finds the bars by their exact gray levels and measures their heights,
  2. OCRs the printed labels with tesseract,
  3. accepts a label only if it agrees with the bar height, and checks the four
     blocks of a day against the printed daily sum,
  4. merges the values of all charts showing a week (majority vote),
  5. validates itself against real portal readings where they overlap
     (the charts from September 2023 on), and finally
  6. writes the reconstructed 6-hour values for the period without portal data.

The output is clearly marked as reconstructed: every row carries
`source=chart_ocr`, and the file name ends in `_reconstructed_6h.csv`.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image

logger = logging.getLogger('reconstruct')

# Rendering constants of render.py
Y_MAX = 6.0                      # axs.set_ylim([0, 6])
BAR_COLORS = {211: 0, 128: 1, 105: 2, 169: 3}  # lightgray, gray, dimgray, darkgray -> block index
BLOCK_HOURS = [(0, 6), (6, 12), (12, 18), (18, 24)]
BAR_WIDTH = 0.25
X_MIN, X_MAX = -0.5, 6.5         # margins(x=0) with 4 bars of width 0.25 around 0..6
LABEL_TOLERANCE = 0.02           # kWh: OCR value vs. calibrated bar height
RAW_TOLERANCE = 0.08             # kWh: OCR value vs. uncalibrated bar height (first pass)
MIN_CALIBRATION_POINTS = 8
SUM_TOLERANCE = 0.03             # kWh: sum of 4 blocks vs. printed daily sum (rounding)

FILE_RE = re.compile(r'^(?P<meter>[A-Z0-9]+)_(?P<start>\d{4}-\d{2}-\d{2})-(?P<stop>\d{4}-\d{2}-\d{2})\.png$')


# -- Image analysis -----------------------------------------------------------

def find_axes(img: np.ndarray) -> list:
    """Returns [(top, bottom, left, right)] pixel bounds of the three stacked subplots."""
    black = img < 40
    h, w = img.shape
    # Long horizontal black runs are the axes frames (top of ax1, two shared borders, bottom of ax3)
    row_runs = black.sum(axis=1)
    candidates = [y for y in range(h) if row_runs[y] > w * 0.6]
    lines = []
    for y in candidates:
        if lines and y - lines[-1][-1] <= 1:
            lines[-1].append(y)
        else:
            lines.append([y])
    frame_rows = [int(np.mean(l)) for l in lines]
    if len(frame_rows) < 4:
        raise ValueError(f'expected 4 horizontal frame lines, found {frame_rows}')
    frame_rows = frame_rows[:4]
    top, bottom = frame_rows[0], frame_rows[-1]
    col_runs = black[top:bottom + 1].sum(axis=0)
    vertical = [x for x in range(w) if col_runs[x] > (bottom - top) * 0.9]
    left, right = min(vertical), max(vertical)
    return [(frame_rows[i], frame_rows[i + 1], left, right) for i in range(3)]


def data_to_px_x(ax, xval: float) -> float:
    top, bottom, left, right = ax
    return left + (xval - X_MIN) / (X_MAX - X_MIN) * (right - left)


def px_to_kwh(ax, y_top: float) -> float:
    top, bottom, left, right = ax
    return (bottom - y_top) / (bottom - top) * Y_MAX


def subpixel_top(img: np.ndarray, x0: int, x1: int, y_top: int, gray: int) -> float:
    """Refines the bar top using the anti-aliased edge pixel above the first solid row."""
    if y_top <= 0:
        return float(y_top)
    edge = float(np.median(img[y_top - 1, x0:x1 + 1]))  # median ignores text strokes
    coverage = (255.0 - edge) / (255.0 - gray)  # 0 = white, 1 = fully bar colored
    coverage = min(max(coverage, 0.0), 1.0)
    return y_top - coverage


def bar_mask(col: np.ndarray, gray: int) -> np.ndarray:
    """True for rows that show the bar color, either pure or seen through the
    semi-transparent white box that carries the daily total (alpha 0.3).
    The digits of that total cut through the bar, so a row counts when most of
    its pixels have the bar color."""
    blended = 0.7 * gray + 0.3 * 255
    px = col.astype(float)
    pure = np.abs(px - gray) <= 1
    boxed = np.abs(px - blended) <= 2
    matching = (pure | boxed).sum(axis=1)
    non_text = (px >= 95).sum(axis=1)  # digit strokes and their dark anti-aliasing are ignored
    return (matching >= 3) & (matching >= 0.9 * np.maximum(non_text, 1))


def bar_top_index(match: np.ndarray, box_rows: int) -> Optional[int]:
    """Walks up from the baseline through the contiguous run of bar rows and returns
    the index of the topmost one. Inside the bottom `box_rows` (where the daily
    total is printed over the bars) up to 14 consecutive non-matching rows, the
    height of those digits, are tolerated; above it only 1 (anti-aliasing)."""
    top = None
    gap = 0
    matched = 0
    n = len(match)
    for y in range(n - 1, -1, -1):
        if match[y]:
            top, gap, matched = y, 0, matched + 1
        else:
            gap += 1
            if gap > (14 if n - y <= box_rows else 1):
                break
    return top if matched >= 2 else None


def find_bars(img: np.ndarray, ax) -> dict:
    """Returns {(day, block): (x_center, y_top)} for every bar drawn in the subplot."""
    top, bottom, left, right = ax
    bars = {}
    for gray, block in BAR_COLORS.items():
        for day in range(7):
            xc = data_to_px_x(ax, day + (block - 1.5) * BAR_WIDTH)
            x0, x1 = int(round(xc - 8)), int(round(xc + 8))  # bars are about 21 px wide
            col = img[top + 1:bottom, x0:x1 + 1]
            idx = bar_top_index(bar_mask(col, gray), box_rows=int(0.5 / Y_MAX * (bottom - top)))
            if idx is None:
                continue
            y_top = top + 1 + idx
            bars[(day, block)] = (xc, subpixel_top(img, x0, x1, y_top, gray))
    return bars


def ocr(crop: Image.Image, scale: int = 5, threshold: int = 150) -> Optional[str]:
    """Runs tesseract on a small crop containing one number."""
    big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    big = big.point(lambda p: 255 if p > threshold else 0)
    # Add a white border, tesseract dislikes text touching the edge
    padded = Image.new('L', (big.width + 40, big.height + 40), 255)
    padded.paste(big, (20, 20))
    proc = subprocess.run(
        ['tesseract', 'stdin', 'stdout', '--psm', '7', '-c', 'tessedit_char_whitelist=0123456789.'],
        input=_png_bytes(padded), capture_output=True,
    )
    text = proc.stdout.decode(errors='replace').strip()
    m = re.search(r'\d+\.\d\d', text)
    return m.group(0) if m else None


def _png_bytes(im: Image.Image) -> bytes:
    import io
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


def read_label(pil: Image.Image, xc: float, y_top: float, expected: float) -> list:
    """Reads the rotated value label printed above a bar with a few OCR settings and
    returns the distinct readings that are roughly consistent with the bar height."""
    y = int(round(y_top))
    crop = pil.crop((int(round(xc - 7)), max(0, y - 48), int(round(xc + 7)), max(0, y - 1)))
    crop = crop.rotate(-90, expand=True)  # labels are rotated 90° counter-clockwise
    candidates = []
    for scale, threshold in ((5, 150), (4, 120), (6, 170), (5, 100)):
        text = ocr(crop, scale, threshold)
        if text and abs(float(text) - expected) <= RAW_TOLERANCE and float(text) not in candidates:
            candidates.append(float(text))
        if len(candidates) == 1 and scale == 4:
            break  # two settings agree, good enough
    return candidates


def read_day_sum(pil: Image.Image, ax, day: int) -> Optional[float]:
    """Reads the daily total printed in a box at the bottom of each day group."""
    top, bottom, left, right = ax
    xc = data_to_px_x(ax, day)
    y = bottom - 0.15 / Y_MAX * (bottom - top)
    crop = pil.crop((int(xc - 26), int(y - 12), int(xc + 26), int(y + 6)))
    text = ocr(crop, scale=4)
    return float(text) if text else None


def analyze_chart(path: Path) -> list:
    """Returns [(week_offset, day, block, value, method, day_sum)] for one chart.

    The pixel-to-kWh mapping is calibrated per chart with the labels that were read
    unambiguously, so that bars whose label is unreadable still get a value that is
    accurate to about 0.01 kWh."""
    pil = Image.open(path).convert('L')
    img = np.array(pil)
    axes = find_axes(img)
    raw = []
    for week_offset, ax in enumerate(axes):
        bars = find_bars(img, ax)
        day_sums = {day: read_day_sum(pil, ax, day) for day in range(7) if any((day, b) in bars for b in range(4))}
        for (day, block), (xc, y_top) in sorted(bars.items()):
            height_value = px_to_kwh(ax, y_top)
            candidates = read_label(pil, xc, y_top, height_value)
            raw.append((week_offset, day, block, height_value, candidates, day_sums.get(day)))

    pairs = [(h, c[0]) for _, _, _, h, c, _ in raw if len(c) == 1]
    if len(pairs) >= MIN_CALIBRATION_POINTS:
        xs, ys = zip(*pairs)
        a, b = np.polyfit(xs, ys, 1)
        resid = [abs(a * x + b - y) for x, y in pairs]
        logger.debug('%s: calibration value = %.4f * height + %.4f from %d labels, median residual %.3f',
                     path.name, a, b, len(pairs), float(np.median(resid)))
    else:
        a, b = 1.0, 0.0
        logger.warning('%s: only %d readable labels, bar heights are not calibrated', path.name, len(pairs))

    result = []
    for week_offset, day, block, height_value, candidates, day_sum in raw:
        calibrated = a * height_value + b
        matching = [c for c in candidates if abs(c - calibrated) <= LABEL_TOLERANCE]
        if len(matching) == 1:
            result.append((week_offset, day, block, matching[0], 'label', day_sum))
        else:
            result.append((week_offset, day, block, round(max(calibrated, 0.0), 2), 'height', day_sum))
    return result


# -- Consolidation --------------------------------------------------------------

def _analyze(path: Path):
    try:
        return path, analyze_chart(path)
    except Exception as err:  # noqa: BLE001
        return path, err


def collect(chart_dir: Path, meter: str, tz: str, before: Optional[date] = None) -> dict:
    """Returns {(date, block): {week_offset: (value, method)}} over all charts.

    week_offset 0 means the chart was rendered right after that week (most reliable,
    see README), 1 and 2 are the "last week" / "week before last" panels of later charts.
    method is 'label' (OCR agreed with bar height) or 'height' (bar height only)."""
    found = defaultdict(dict)
    stats = Counter()
    files = sorted(p for p in chart_dir.glob(f'{meter}_*.png') if FILE_RE.match(p.name))
    if before:
        # a chart shows its own week and the two before; keep a few weeks past the
        # cut-off so the result can be validated against real readings
        files = [p for p in files
                 if date.fromisoformat(FILE_RE.match(p.name).group('start')) <= before + timedelta(weeks=6)]
    logger.info('Analyzing %d charts in %s', len(files), chart_dir)
    from multiprocessing import Pool, cpu_count
    with Pool(min(cpu_count(), 8)) as pool:
        analyzed = pool.map(_analyze, files)
    for path, rows in analyzed:
        m = FILE_RE.match(path.name)
        week_start = date.fromisoformat(m.group('start'))
        if isinstance(rows, Exception):
            logger.warning('%s: %s', path.name, rows)
            continue
        per_day = defaultdict(dict)
        for week_offset, day, block, value, method, day_sum in rows:
            stats['bars'] += 1
            d = week_start - timedelta(weeks=week_offset) + timedelta(days=day)
            if method == 'height':
                stats['height_only'] += 1
                logger.debug('%s w-%d %s block %d: no readable label, using calibrated bar height %.2f',
                             path.name, week_offset, d, block, value)
            per_day[(week_offset, d)][block] = (value, method, day_sum)
        for (week_offset, d), blocks in per_day.items():
            day_sum = next(iter(blocks.values()))[2]
            total = sum(v for v, _, _ in blocks.values())
            if len(blocks) == 4 and day_sum is not None and abs(total - day_sum) > SUM_TOLERANCE:
                if all(method == 'label' for _, method, _ in blocks.values()):
                    # every label agreed with its calibrated bar height, so the printed sum was misread
                    logger.debug('%s w-%d %s: blocks sum %.2f != printed %.2f (labels kept)',
                                 path.name, week_offset, d, total, day_sum)
                    stats['sum_mismatch'] += 1
                # one unreadable label: derive it from the printed daily sum
                for b, (v, method, _) in blocks.items():
                    if method == 'height':
                        derived = round(day_sum - sum(v2 for b2, (v2, _, _) in blocks.items() if b2 != b), 2)
                        if abs(derived - v) <= LABEL_TOLERANCE * 2:
                            blocks[b] = (derived, 'sum', day_sum)
                            stats['derived_from_sum'] += 1
            for b, (v, method, _) in blocks.items():
                found[(d, b)][week_offset] = (v, method)
    logger.info('%d bars found: %d labels read, %d derived from the daily sum, %d bar-height only, '
                '%d days where the printed sum was misread', stats['bars'], stats['bars'] - stats['height_only'],
                stats['derived_from_sum'], stats['height_only'] - stats['derived_from_sum'], stats['sum_mismatch'])
    return found


def consensus(found: dict) -> dict:
    """Picks one value per (date, block): the chart rendered right after the week wins,
    later renderings are only used when it is missing. Returns {(date, block): (value, method)}."""
    result = {}
    disagreements = 0
    for key, by_offset in found.items():
        offsets = sorted(by_offset)
        # prefer label-based readings of the earliest rendering
        best = min(offsets, key=lambda o: (by_offset[o][1] != 'label', o))
        result[key] = by_offset[best]
        values = {round(v, 2) for v, _ in by_offset.values()}
        if len(values) > 1:
            disagreements += 1
            logger.debug('%s block %d: renderings disagree %s, keeping w-%d',
                         key[0], key[1], by_offset, best)
    logger.info('%d values, %d where later renderings disagree with the first one', len(result), disagreements)
    return result


# -- Validation against portal data ---------------------------------------------

def load_portal_blocks(readings_csv: Path, tz: str, shift_minutes: int) -> dict:
    """6-hour sums of the real quarter-hour readings, blocks starting at 00:00+shift."""
    zone = ZoneInfo(tz)
    blocks = defaultdict(float)
    with readings_csv.open(newline='') as fh:
        reader = csv.DictReader(fh, delimiter=';')
        for row in reader:
            t = datetime.fromisoformat(row['start_local']) - timedelta(minutes=shift_minutes)
            blocks[(t.date(), t.hour // 6)] += float(row['kwh'])
    return blocks


def validate(found: dict, values: dict, readings_csv: Path, tz: str) -> None:
    portal = load_portal_blocks(readings_csv, tz, 15)
    common = [k for k in values if k in portal]
    if not common:
        logger.info('No overlap with portal data to validate against')
        return
    for offset in (0, 1, 2):
        pairs = [(found[k][offset][0], portal[k]) for k in common if offset in found[k]]
        if pairs:
            diffs = [abs(a - b) for a, b in pairs]
            ok = sum(1 for d in diffs if d <= 0.011)
            logger.info('Validation w-%d panels vs. portal: %d/%d within 0.01 kWh, max deviation %.3f',
                        offset, ok, len(pairs), max(diffs))
    diffs = [abs(values[k][0] - portal[k]) for k in common]
    ok = sum(1 for d in diffs if d <= 0.011)
    logger.info('Validation of the final selection vs. portal: %d/%d within 0.01 kWh, max deviation %.3f, mean %.4f',
                ok, len(common), max(diffs), sum(diffs) / len(diffs))
    for k in common:
        if abs(values[k][0] - portal[k]) > 0.011:
            logger.warning('  %s block %d: reconstructed %.2f, portal %.2f (%s)', k[0], k[1], values[k][0], portal[k], found[k])


# -- Output ---------------------------------------------------------------------

# Notes must not contain commas or semicolons, the files are semicolon-separated
METHOD_NOTES = {
    'label': 'value label read from chart by OCR and verified against bar height',
    'sum': 'label unreadable / derived from the printed daily sum and the other three blocks',
    'height': 'label unreadable / estimated from bar height (about +/-0.02 kWh)',
    'manual': 'value label read by a human from the chart (corrections file)',
}


def apply_corrections(values: dict, path: Path, tz: str, shift_minutes: int) -> int:
    """Overrides values with a human-read corrections file: CSV with a header and the
    columns start_local;kwh (start_local as in the output file)."""
    if not path.exists():
        return 0
    n = 0
    with path.open(newline='') as fh:
        for row in csv.DictReader(fh, delimiter=';'):
            start = datetime.fromisoformat(row['start_local'])
            block = (start.hour * 60 + start.minute - shift_minutes) // 360
            key = (start.date(), block)
            values[key] = (float(row['kwh'].replace(',', '.')), 'manual')
            n += 1
    logger.info('Applied %d manual corrections from %s', n, path)
    return n


def dst_switch_in_week(d: date, tz: str) -> bool:
    zone = ZoneInfo(tz)
    monday = d - timedelta(days=d.weekday())
    a = datetime.combine(monday, datetime.min.time(), tzinfo=zone).utcoffset()
    b = datetime.combine(monday + timedelta(days=7), datetime.min.time(), tzinfo=zone).utcoffset()
    return a != b


def write_output(values: dict, path: Path, before: Optional[date], tz: str, shift_minutes: int) -> int:
    zone = ZoneInfo(tz)
    rows = 0
    with path.open('w', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(['start_local', 'end_local', 'kwh', 'source', 'method', 'note'])
        for (d, block) in sorted(values):
            if before and d >= before:
                continue
            value, method = values[(d, block)]
            h0, h1 = BLOCK_HOURS[block]
            start = datetime(d.year, d.month, d.day, h0, shift_minutes, tzinfo=zone)
            end = datetime.combine(d + timedelta(days=1) if h1 == 24 else d, datetime.min.time(), tzinfo=zone)
            end = end.replace(hour=0 if h1 == 24 else h1, minute=shift_minutes)
            note = METHOD_NOTES[method]
            if dst_switch_in_week(d, tz):
                note += ' / DST switch in this week so block boundaries may be off by one hour'
            writer.writerow([start.isoformat(), end.isoformat(), f'{value:.2f}', 'chart_ocr', method, note])
            rows += 1
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--charts', required=True, help='folder with the weekly chart PNGs')
    parser.add_argument('--meter', required=True, help='meter id (file name prefix)')
    parser.add_argument('--tz', default='Europe/Vienna')
    parser.add_argument('--readings', help='readings CSV from `databot csv`, used for validation and cut-off')
    parser.add_argument('--before', help='only output days before this date (YYYY-MM-DD); '
                                         'default: first day in --readings')
    parser.add_argument('-o', '--output', help='output file (default <charts>/<meter>_reconstructed_6h.csv)')
    parser.add_argument('--corrections', help='CSV with human-read values (start_local;kwh), '
                                              'default <charts>/<meter>_reconstructed_corrections.csv')
    parser.add_argument('--shift', type=int, default=15,
                        help='minutes the chart blocks are shifted from the full hour (default 15, see README)')
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')

    chart_dir = Path(args.charts)
    before = date.fromisoformat(args.before) if args.before else None
    readings = Path(args.readings) if args.readings else None
    if readings and readings.exists() and before is None:
        with readings.open(newline='') as fh:
            first = next(csv.DictReader(fh, delimiter=';'), None)
        if first:
            before = datetime.fromisoformat(first['start_local']).date()
            logger.info('Portal readings start on %s, reconstructing the days before', before)
    found = collect(chart_dir, args.meter, args.tz, before)
    values = consensus(found)
    logger.info('%d six-hour values reconstructed for %d days', len(values), len({d for d, _ in values}))
    if readings and readings.exists():
        validate(found, values, readings, args.tz)
    corrections = Path(args.corrections) if args.corrections else chart_dir / f'{args.meter}_reconstructed_corrections.csv'
    apply_corrections(values, corrections, args.tz, args.shift)
    output = Path(args.output) if args.output else chart_dir / f'{args.meter}_reconstructed_6h.csv'
    n = write_output(values, output, before, args.tz, args.shift)
    logger.info('Wrote %d reconstructed rows%s to %s', n, f' before {before}' if before else '', output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
