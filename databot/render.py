"""Renders the weekly consumption chart from the CSV data.

Data source: <EXPORT_DIR>/<meterId>_all.csv (reconstructed 6h blocks followed by
the measured quarter hours, written by `databot csv` / `databot merge`), falling
back to <meterId>_readings.csv. No database involved.
"""

import csv
import logging
import os
from argparse import ArgumentParser
from cmath import nan
from datetime import date, datetime, timedelta
from glob import glob
from os.path import exists
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

base_path = os.environ.get('EXPORT_DIR', 'export').rstrip('/') + '/'

# Load environment variables
meter_id = os.environ['METER_ID']
tz = os.environ['TZ']
zone = ZoneInfo(tz)

labels = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
x_label_locations = np.arange(len(labels))  # the label locations
width = 0.25  # the width of the bars

_readings = None  # list of (start epoch seconds, local start datetime, kwh), sorted


def load_readings():
    """Loads the CSV once. Prefers the unified file (it also covers the reconstructed
    history), falls back to the plain readings file."""
    global _readings
    if _readings is not None:
        return _readings
    for name in (f'{meter_id}_all.csv', f'{meter_id}_readings.csv'):
        path = Path(base_path) / name
        if path.exists():
            break
    else:
        raise FileNotFoundError(f'no data file found in {base_path}, run `databot csv` first')
    rows = []
    with path.open(newline='') as fh:
        for r in csv.DictReader(fh, delimiter=';'):
            try:
                start = datetime.fromisoformat(r['start_local'])
                rows.append((start.timestamp(), start.astimezone(zone), float(r['kwh'])))
            except (KeyError, ValueError):
                continue
    rows.sort()
    logging.debug(f'Loaded {len(rows)} readings from {path}')
    _readings = rows
    return rows


def sum_between(start, stop):
    """Sum of kWh of all intervals starting in [start, stop). Returns nan if there are none."""
    import bisect
    rows = load_readings()
    keys = [r[0] for r in rows]
    lo, hi = bisect.bisect_left(keys, start.timestamp()), bisect.bisect_left(keys, stop.timestamp())
    if hi <= lo:
        return nan
    return sum(r[2] for r in rows[lo:hi])


def week_start(weeks_back, day):
    """Monday 00:00 local of the week `weeks_back` weeks before the week containing `day`."""
    day = day.date() if isinstance(day, datetime) else day
    monday = day - timedelta(days=day.weekday(), weeks=weeks_back)
    return datetime(monday.year, monday.month, monday.day, tzinfo=zone)


def local_plus(t, **delta):
    """Adds a calendar delta in local wall-clock time (DST-safe)."""
    naive = t.replace(tzinfo=None) + timedelta(**delta)
    return naive.replace(tzinfo=zone)


def get_bar_chart_values(weeks_back, date):
    """28 values: four 6-hour sums (00-06, 06-12, 12-18, 18-24 local) for each day of the week,
    nan where there is no data."""
    monday = week_start(weeks_back, date)
    values = []
    for day in range(7):
        for block in range(4):
            start = local_plus(monday, days=day, hours=6 * block)
            stop = local_plus(monday, days=day, hours=6 * (block + 1))
            values.append(sum_between(start, stop))
    logging.debug(f'Bar chart values (weeks_back={weeks_back}, date={date}): {values}')
    return values


def get_ytd_statistics(date):
    """Mean daily consumption and total since 1 January of `date`'s year."""
    day = date.date() if isinstance(date, datetime) else date
    first = datetime(day.year, 1, 1, tzinfo=zone)
    daily = []
    d = first
    while d.date() <= day:
        value = sum_between(d, local_plus(d, days=1))
        if value == value:  # not nan
            daily.append(value)
        d = local_plus(d, days=1)
    if not daily:
        return nan, nan
    return sum(daily) / len(daily), sum(daily)


def add_bars(values, axs, ylabel):
    day_part_1_sums = []
    day_part_2_sums = []
    day_part_3_sums = []
    day_part_4_sums = []

    for i in range(len(labels)):
        try:
            day_part_1_sums.append(values[i * 4 + 0])
        except:
            day_part_1_sums.append(nan)
        try:
            day_part_2_sums.append(values[i * 4 + 1])
        except:
            day_part_2_sums.append(nan)
        try:
            day_part_3_sums.append(values[i * 4 + 2])
        except:
            day_part_3_sums.append(nan)
        try:
            day_part_4_sums.append(values[i * 4 + 3])
        except:
            day_part_4_sums.append(nan)

    epsilon = 0.01  # Add a small epsilon to prevent small gaps
    rects1 = axs.bar(x_label_locations - (width * 1.5), day_part_1_sums, width + epsilon,
                     label='00:00-06:00', color='lightgray')
    rects2 = axs.bar(x_label_locations - (width * 0.5), day_part_2_sums, width + epsilon,
                     label='06:00-12:00', color='gray')
    rects3 = axs.bar(x_label_locations + (width * 0.5), day_part_3_sums, width + epsilon,
                     label='12:00-18:00', color='dimgray')
    rects4 = axs.bar(x_label_locations + (width * 1.5), day_part_4_sums, width + epsilon,
                     label='18:00-24:00', color='darkgray')
    axs.set_ylabel(ylabel)
    axs.set_xticks(x_label_locations, labels)

    fmt = '%.2f'
    padding = 2
    axs.bar_label(rects1, rotation='vertical',  padding=padding,  # label_type='center',
                  fmt=fmt, fontsize=4, fontweight='normal')
    axs.bar_label(rects2, rotation='vertical',  padding=padding,  # label_type='center',
                  fmt=fmt, fontsize=4, fontweight='normal')
    axs.bar_label(rects3, rotation='vertical',  padding=padding,  # label_type='center',
                  fmt=fmt, fontsize=4, fontweight='normal')
    axs.bar_label(rects4, rotation='vertical',  padding=padding,  # label_type='center',
                  fmt=fmt, fontsize=4, fontweight='normal')

    # Add daily sums to bottom of each group
    total = 0
    cnt = 0
    for i in range(len(labels)):
        sum = day_part_1_sums[i] + day_part_2_sums[i] + \
            day_part_3_sums[i] + day_part_4_sums[i]
        if sum > 0:
            total += sum
            cnt += 1
            axs.text(x=i, y=0.15, s=fmt % sum, fontsize=5, horizontalalignment='center', bbox=dict(
                facecolor='white', alpha=0.3, boxstyle='round', edgecolor='none', pad=0.2))

    # Add total to right (faking it as an 8th column outside the plot)
    if total > 0 and cnt > 0:
        axs.text(x=7, y=0.2, s='Ø = {:.2f}   Σ = {:.2f}'.format(total / cnt, total),
                 fontsize=6, rotation=90, horizontalalignment='center', verticalalignment='bottom')

    # Avoid that yaxix label sticks on the border
    # axs.yaxis.set_label_coords(-0.1, 0.5)

    # Leave some room above so that the labels above the bar won't overflow the chart
    axs.margins(x=0, y=0.15)

    # Add grid lines to distinguish day groupts
    axs.set_xticks([0.5, 1.5, 2.5, 3.5, 4.5, 5.5], minor=True)
    axs.xaxis.grid(visible=True, linestyle=':', color='black',
                   linewidth=0.8, which='minor')


def render(date=datetime.now(), filename='current.png', title_suffix=''):
    date_str = date.strftime('%Y-%m-%d')
    values_w0 = get_bar_chart_values(weeks_back=0, date=date)
    values_w1 = get_bar_chart_values(weeks_back=1, date=date)
    values_w2 = get_bar_chart_values(weeks_back=2, date=date)
    has_data = any(v == v for v in values_w0 + values_w1 + values_w2)  # any value that is not nan

    if not has_data:
        return False

    logging.info(f'Rendering chart {date_str} started')

    # Make it appear a little different
    matplotlib.rcParams['font.family'] = ['monospace']
    matplotlib.rcParams['font.size'] = 6
    matplotlib.rcParams['font.weight'] = 'bold'

    # Plot bar data
    fig, (axs1, axs2, axs3) = plt.subplots(
        3, 1, sharex=True, sharey=True)
    plt.subplots_adjust(hspace=0, top=0.91)

    add_bars(values=values_w0, axs=axs1, ylabel='Aktuelle Woche')
    add_bars(values=values_w1, axs=axs2, ylabel='Letzte Woche')
    add_bars(values=values_w2, axs=axs3, ylabel='Vorletzte Woche')

    ytd_mean, ytd_sum = get_ytd_statistics(date)
    axs1.set_title('YTD   Ø = {:.2f}   Σ = {:.2f}'.format(
        ytd_mean, ytd_sum), fontsize=6, loc='left')

    axs3.legend(loc='upper center', bbox_to_anchor=(
        0.5, -0.2), ncol=4, frameon=False, fontsize=6, handlelength=1)
    
    axs1.set_ylim([0, 6])
    axs2.set_ylim([0, 6])
    axs3.set_ylim([0, 6])

    # Remove `0` and `6` tick to avoid layout collisions with neighbor charts
    for ax in [axs1, axs2, axs3]:
        ax.yaxis.get_major_ticks()[0].label1.set_visible(False)
        ax.yaxis.get_major_ticks()[0].tick1line.set_visible(False)

        ax.yaxis.get_major_ticks()[6].label1.set_visible(False)
        ax.yaxis.get_major_ticks()[6].tick1line.set_visible(False)

        ax.xaxis.get_minor_ticks()[0].tick1line.set_visible(False)
        ax.xaxis.get_minor_ticks()[1].tick1line.set_visible(False)
        ax.xaxis.get_minor_ticks()[2].tick1line.set_visible(False)
        ax.xaxis.get_minor_ticks()[3].tick1line.set_visible(False)
        ax.xaxis.get_minor_ticks()[4].tick1line.set_visible(False)
        ax.xaxis.get_minor_ticks()[5].tick1line.set_visible(False)

    os.makedirs(base_path, exist_ok=True)

    fig.suptitle('Stromverbrauch (kWh)' + title_suffix)
    # fig.tight_layout()

    # Portrait 758 x 1024 px @ 212 dpi (the size of the e-ink display the chart was designed for)
    dpi = 212
    # Some buffer needed to actually get 758w ...
    fig.set_size_inches(760 / dpi, 1024 / dpi)
    # Write to a temporary file and replace atomically: readers never see a
    # half-written image, and overwriting files in place can fail on synced
    # folders (iCloud Drive returns EDEADLK through a Docker bind mount).
    tmp_path = base_path + '.' + filename + '.tmp.png'
    plt.savefig(tmp_path, dpi=dpi)

    # Convert to greyscale (e-ink friendly, and the archived charts look the same)
    img = Image.open(tmp_path).convert('L')
    img.save(tmp_path)
    os.replace(tmp_path, base_path + filename)

    logging.info(f'Rendering chart {date_str} done')
    plt.close()

    return True


def clean():
    for file in glob(base_path + f'{meter_id}_*-*.png'):
        try:
            os.remove(file)
            logging.info(f'Deleted {file}')
        except:
            logging.error(f'Deleting {file} failed')


def archive():
    logging.info('Archiving charts started')

    date_format = '%Y-%m-%d'
    delta_week = 1
    has_next = True
    max_failed_attempts = 1
    failed_attempts = 0
    allowed_skip_weeks = 3
    while has_next:
        date = datetime.now() - timedelta(weeks=delta_week)
        start = date - timedelta(days=date.weekday())
        stop = start + timedelta(days=6)
        try:
            filename = f'{meter_id}_{start.strftime(date_format)}-{stop.strftime(date_format)}.png'
            if exists(base_path + filename) or not render(date=stop, filename=filename, title_suffix=f' {start.strftime(date_format)} - {stop.strftime(date_format)}'):
                allowed_skip_weeks -= 1
            delta_week += 1
        except Exception as err:
            failed_attempts += 1
            logging.error(err)

        if failed_attempts >= max_failed_attempts or allowed_skip_weeks <= 0:
            logging.info(
                'Stopping archiving since no more data is present')
            has_next = False

    logging.info('Archiving charts done')


def main():
    logging.getLogger().setLevel(os.environ.get('LOGLEVEL', 'INFO').upper())
    logging.basicConfig(
        format='[ %(asctime)s %(levelname)s ] %(message)s')

    parser = ArgumentParser()
    parser.add_argument('-a', '--archive', default=False,
                        action='store_true', help='Archive previous charts')
    parser.add_argument('-c', '--clean', default=False,
                        action='store_true', help='Clean previous charts')
    parser.add_argument('-d', '--day', type=str, help='Load specific day')
    args = parser.parse_args()

    try:
        if args.clean:
            clean()
        elif args.archive:
            archive()
        elif args.day:
            try:
                day = datetime.strptime(args.day, '%d.%m.%Y')
                logging.info(f'should load {args.day}')
            except:
                raise Exception(
                    'Cannot parse argument, format must be DD.MM.YYYY')

            date_format = '%Y-%m-%d'
            start = day - timedelta(days=day.weekday())
            stop = start + timedelta(days=6)
            filename = f'{meter_id}_{start.strftime(date_format)}-{stop.strftime(date_format)}.png'
            render(date=stop, filename=filename,
                   title_suffix=f' {start.strftime(date_format)} - {stop.strftime(date_format)}')
        else:
            render()

    except Exception as err:
        logging.error(err)
        exit(1)


if __name__ == '__main__':
    main()
