#!/usr/bin/env python3
"""Command line for the readings archive of one LINZ NETZ metering point (lib/homebase).

Commands:
  csv           (default) create or extend <EXPORT_DIR>/<meterId>_readings.csv straight from the
                portal: the first run downloads everything, later runs re-load a few days of
                overlap (the portal corrects values retroactively), re-fetch incomplete days,
                extend backwards if the portal has older data, then rewrite the unified file
  merge         write <meterId>_all.csv (reconstructed history + measured quarter hours)
  check         report days with missing quarter-hour intervals in the readings file
  disaggregate  split the reconstructed 6-hour blocks into quarter hours by a typical profile
  fuse          one hourly series from RF (HA history download), portal and reconstruction

Raw portal exports are archived under <EXPORT_DIR>/csv/. Charts: render.py, the chart
reconstruction: reconstruct.py, the Home Assistant import test: ha_spike.py.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from homebase import disaggregate, fusion, readings
from homebase.portal import DATE_FORMAT, LinzNetzError, LoginError

logger = logging.getLogger('databot')


@dataclass
class Config:
    username: str
    password: str
    meter_id: str
    tz: str
    export_dir: Path


def load_config(env_file: Optional[str], need_login: bool) -> Config:
    if env_file:
        load_dotenv(env_file)
    required = ('USERNAME', 'PASSWORD', 'METER_ID', 'TZ') if need_login else ('METER_ID', 'TZ')
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing environment variable(s): {", ".join(missing)}')
    return Config(
        username=os.environ.get('USERNAME', ''),
        password=os.environ.get('PASSWORD', ''),
        meter_id=os.environ['METER_ID'],
        tz=os.environ['TZ'],
        export_dir=Path(os.environ.get('EXPORT_DIR', 'export')),
    )


def readings_file(args, config: Config) -> Path:
    return Path(args.file) if args.file else readings.readings_path(config.export_dir, config.meter_id)


# -- Commands -------------------------------------------------------------------

def cmd_csv(args, config: Config) -> int:
    readings.update_from_portal(config.username, config.password, config.meter_id, config.tz,
                                config.export_dir, readings_file(args, config), args.overlap,
                                args.chunk_days, args.stop_after_empty)
    return 0


def cmd_check(args, config: Config) -> int:
    path = readings_file(args, config)
    rows = readings.read_csv_file(path)
    if not rows:
        logger.error('%s is missing or empty, run `csv` first', path)
        return 1
    zone = ZoneInfo(config.tz)
    first, last = datetime.fromtimestamp(min(rows), zone), datetime.fromtimestamp(max(rows), zone)
    today = datetime.now(zone).date()
    stop = min(last.date(), today - timedelta(days=1))
    bad = readings.incomplete_days_in_rows(rows, config.tz, stop)
    logger.info('%s: %d readings, %s - %s', path, len(rows), first.strftime('%d.%m.%Y %H:%M'),
                last.strftime('%d.%m.%Y %H:%M'))
    if bad:
        for d in bad:
            logger.warning('%s incomplete', d.strftime(DATE_FORMAT))
        logger.info('%d incomplete day(s) up to %s, run `csv` to re-fetch them', len(bad), stop.strftime(DATE_FORMAT))
        return 1
    logger.info('All %d days up to %s complete', (stop - first.date()).days + 1, stop.strftime(DATE_FORMAT))
    return 0


def cmd_merge(args, config: Config) -> int:
    path = readings_file(args, config)
    rows = readings.read_csv_file(path)
    if not rows:
        logger.error('%s is missing or empty, run `csv` first', path)
        return 1
    out = readings.unified_path(config.export_dir, config.meter_id)
    n = readings.write_unified(config.export_dir, config.meter_id, config.tz, rows, out)
    logger.info('Wrote %d rows to %s', n, out)
    return 0


def cmd_disaggregate(args, config: Config) -> int:
    zone = ZoneInfo(config.tz)
    folder = config.export_dir
    readings_path = readings.readings_path(folder, config.meter_id)
    blocks_path = folder / f'{config.meter_id}_reconstructed_6h.csv'
    out_path = folder / f'{config.meter_id}_reconstructed_15min.csv'
    if not readings_path.exists():
        logger.error('%s not found, run `csv` first', readings_path)
        return 1
    measured = disaggregate.read_readings(readings_path)
    window = (args.profile_from, args.profile_to)

    result = disaggregate.backtest(measured, zone, window, (args.backtest_from, args.backtest_to))
    logger.info('Backtest %s to %s (left out of the profile): %d blocks, %d hours, %.1f kWh',
                args.backtest_from, args.backtest_to, result['blocks'], result['hours'], result['actual_kwh'])
    for name in ('profile', 'uniform'):
        logger.info('  %-7s split: hourly mean abs error %.3f kWh, %.1f%% of the consumption',
                    name, result[name]['mae_kwh'], 100 * result[name]['wape'])
    if args.backtest:
        return 0

    if not blocks_path.exists():
        logger.error('%s not found, run `reconstruct` first', blocks_path)
        return 1
    blocks = disaggregate.read_blocks(blocks_path)
    profile = disaggregate.build_profile(measured, zone, *window)
    if not profile:
        logger.error('no readings in the profile window %s to %s', *window)
        return 1
    rows = disaggregate.disaggregate(blocks, profile, zone, window)
    disaggregate.write_rows(out_path, rows)
    logger.info('Split %d blocks (%.2f kWh) into %d quarter hours (%.3f kWh) -> %s', len(blocks),
                sum(b.kwh for b in blocks), len(rows), sum(float(r[2]) for r in rows), out_path)
    return 0


def cmd_fuse(args, config: Config) -> int:
    zone = ZoneInfo(config.tz)
    folder = config.export_dir
    readings_path = readings.readings_path(folder, config.meter_id)
    if not readings_path.exists():
        logger.error('%s not found, run `csv` first', readings_path)
        return 1
    portal = fusion.read_portal(readings_path)
    reconstructed = fusion.read_reconstructed(folder / f'{config.meter_id}_reconstructed_15min.csv')

    rf: dict = {}
    if args.rf:
        samples = fusion.read_rf_history(Path(args.rf), args.rf_entity)
        cleaning = fusion.clean_counter(samples, args.max_kw)
        rf = fusion.rf_hourly(cleaning.accepted, timedelta(minutes=args.max_gap_minutes), args.max_kw)
        logger.info('RF: %d readings, %d rejected in %d dropped segment(s), %d hours with a value',
                    len(samples), cleaning.rejected, len(cleaning.dropped_segments), len(rf))
        for first, last, n in cleaning.dropped_segments:
            logger.info('  dropped %s to %s (%d readings)', first.astimezone(zone).isoformat(timespec='minutes'),
                        last.astimezone(zone).isoformat(timespec='minutes'), n)
        v = fusion.validate(portal, rf, zone)
        if v.hours:
            logger.info('RF vs portal: %d hours, mean abs deviation %.0f Wh per hour, total %.3f vs %.3f kWh (%+.2f%%)',
                        v.hours, v.mean_abs_wh, v.rf_wh / 1000, v.portal_wh / 1000,
                        100 * (v.rf_wh - v.portal_wh) / v.portal_wh if v.portal_wh else 0)
            logger.info('  %d complete day(s) compared, %d outside the tolerance', len(v.days), len(v.bad_days))
            for day, p, r in v.bad_days:
                logger.warning('  %s: RF %.3f kWh, portal %.3f kWh', day, r / 1000, p / 1000)
        else:
            logger.info('RF vs portal: no common hours yet')

    hours = fusion.fuse(portal, rf, reconstructed)
    out = folder / f'{config.meter_id}_fused_1h.csv'
    fusion.write_fused(out, hours, zone)
    counts: dict = defaultdict(int)
    for h in hours:
        counts[h.source] += 1
    span = round((hours[-1].start - hours[0].start) / fusion.HOUR) + 1 if hours else 0
    logger.info('Fused %d hours (%s), %d hour(s) without data, total %.3f kWh -> %s', len(hours),
                ', '.join(f'{s} {counts[s]}' for s in fusion.SOURCES if counts[s]), span - len(hours),
                hours[-1].sum_wh / 1000 if hours else 0, out)
    flagged = [h for h in hours if h.flag]
    if flagged:
        logger.warning('%d RF hour(s) deviate from the portal beyond the tolerance (RF kept):', len(flagged))
        for h in flagged[:20]:
            logger.warning('  %s: RF %.3f kWh, portal %.3f kWh', h.start.astimezone(zone).isoformat(timespec='minutes'),
                           h.wh / 1000, h.portal_wh / 1000)
    return 0


# -- Main ---------------------------------------------------------------------

def parse_date(value: str) -> date:
    for fmt in (DATE_FORMAT, '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f'{value!r} is not a date (use DD.MM.YYYY or YYYY-MM-DD)')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='LINZ NETZ smart meter data bot')
    parser.add_argument('-c', '--config', help='path to .env file')
    parser.add_argument('-d', '--debug', action='store_true', help='debug logging')
    parser.add_argument('--chunk-days', type=int, default=readings.DEFAULT_CHUNK_DAYS,
                        help=f'days per portal request (default {readings.DEFAULT_CHUNK_DAYS})')
    parser.set_defaults(command='csv', file=None, overlap=readings.DEFAULT_OVERLAP_DAYS,
                        stop_after_empty=readings.DEFAULT_EMPTY_CHUNKS_TO_STOP)
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('csv', help='create or extend the readings CSV from the portal (default)')
    p.add_argument('-f', '--file', help='CSV file to create or extend (default: <EXPORT_DIR>/<meterId>_readings.csv)')
    p.add_argument('--overlap', type=int, default=readings.DEFAULT_OVERLAP_DAYS,
                   help=f'days to re-fetch before the last reading in the file (default {readings.DEFAULT_OVERLAP_DAYS})')
    p.add_argument('--stop-after-empty', type=int, default=readings.DEFAULT_EMPTY_CHUNKS_TO_STOP,
                   help='stop looking for older data after this many consecutive empty ranges')

    p = sub.add_parser('merge', help='write <meterId>_all.csv from the readings CSV and the reconstruction')
    p.add_argument('-f', '--file', help='readings CSV (default: <EXPORT_DIR>/<meterId>_readings.csv)')

    p = sub.add_parser('check', help='report days with missing quarter-hour intervals')
    p.add_argument('-f', '--file', help='readings CSV (default: <EXPORT_DIR>/<meterId>_readings.csv)')

    p = sub.add_parser('disaggregate', help='split the reconstructed 6h blocks into quarter hours')
    p.add_argument('--profile-from', type=date.fromisoformat, default=disaggregate.DEFAULT_PROFILE_FROM)
    p.add_argument('--profile-to', type=date.fromisoformat, default=disaggregate.DEFAULT_PROFILE_TO)
    p.add_argument('--backtest', action='store_true', help='only run the backtest, write nothing')
    p.add_argument('--backtest-from', type=date.fromisoformat, default=disaggregate.DEFAULT_BACKTEST_FROM)
    p.add_argument('--backtest-to', type=date.fromisoformat, default=disaggregate.DEFAULT_BACKTEST_TO)

    p = sub.add_parser('fuse', help='fuse RF, portal and reconstructed readings into hours')
    p.add_argument('--rf', help='Home Assistant history download of the RF energy sensor (CSV)')
    p.add_argument('--rf-entity', help='entity ID in the history file, if it holds several')
    p.add_argument('--max-kw', type=float, default=fusion.DEFAULT_MAX_KW,
                   help=f'fastest plausible consumption in kW (default {fusion.DEFAULT_MAX_KW:g})')
    p.add_argument('--max-gap-minutes', type=int, default=int(fusion.DEFAULT_MAX_GAP.total_seconds() // 60),
                   help='longest RF silence that is still interpolated')
    return parser


COMMANDS = {'csv': cmd_csv, 'merge': cmd_merge, 'check': cmd_check,
            'disaggregate': cmd_disaggregate, 'fuse': cmd_fuse}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='[ %(asctime)s %(levelname)s ] %(message)s')
    if not args.debug:
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    command = args.command or 'csv'
    config = load_config(args.config, need_login=command == 'csv')
    try:
        return COMMANDS[command](args, config)
    except LoginError as err:
        logger.error('Login failed: %s', err)
        return 2
    except LinzNetzError as err:
        logger.error('Portal error: %s', err)
        return 1


if __name__ == '__main__':
    sys.exit(main())
