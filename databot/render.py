import logging
import os
from argparse import ArgumentParser
from cmath import nan
from datetime import datetime, timedelta
from glob import glob
from os.path import exists

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pytz
from influxdb_client import InfluxDBClient, QueryApi
from PIL import Image

base_path = 'export/'

# Load environment variables
meter_id = os.environ['METER_ID']
tz = os.environ['TZ']
bucket = os.environ['INFLUX_BUCKET']
org = os.environ['INFLUX_ORG']
token = os.environ['INFLUX_TOKEN']
url = os.environ['INFLUX_URL']

labels = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
x_label_locations = np.arange(len(labels))  # the label locations
width = 0.25  # the width of the bars

influxdb_client = InfluxDBClient(
    url=url,
    token=token,
    org=org
)
query_api = QueryApi(influxdb_client)


def get_datetime_range(weeks_back, date):
    date = date.replace(hour=0, minute=0, second=0, microsecond=0)
    start = date - timedelta(days=date.weekday(), weeks=weeks_back)
    stop = start + timedelta(weeks=1)

    # Add 15 min to be coherent with NÖ Netz interval
    start += timedelta(minutes=15)
    stop += timedelta(minutes=15)

    # UTC offset - 15min for correct interval
    offset = '-{:.0f}m'.format(pytz.timezone(tz).localize(
        date).utcoffset().total_seconds() / 60 - 15)

    # Convert to UTC as it's stored in DB
    start = pytz.timezone(tz).localize(start).astimezone(pytz.UTC)
    stop = pytz.timezone(tz).localize(stop).astimezone(pytz.UTC)

    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), stop.strftime("%Y-%m-%dT%H:%M:%SZ"), offset


def get_line_chart_values(weeks_back, date):
    start, stop, offset = get_datetime_range(weeks_back, date)

    # '|> map(fn: (r) => ({r with _value: r._value * 1000.0 })) ' -> add if you want Wh instead of kWh
    flux_query = f'from(bucket: "{bucket}") ' \
        f'|> range(start: time(v: "{start}"), stop: time(v: "{stop}")) ' \
        f'|> filter(fn: (r) => r._measurement == "meteredValues" and r._field == "value")' \
        f'|> aggregateWindow(every: 1h, createEmpty: false, offset: {offset}, fn: sum)'

    logging.debug(
        f'Query get_line_chart_values (weeks_back={str(weeks_back)}, date={str(date)}):\n{flux_query}')

    response = query_api.query(flux_query)
    if len(response) == 0 or len(response[0].records) == 0:
        logging.warning(f'No line chart data between {start} and {stop}')
        return []

    values = list(map(lambda r: r.get_value(), response[0].records))

    return values


def get_bar_chart_values(weeks_back, date):
    start, stop, offset = get_datetime_range(weeks_back, date)

    # '|> map(fn: (r) => ({r with _value: r._value * 1000.0 })) ' -> add if you want Wh instead of kWh
    flux_query = f'from(bucket: "{bucket}") ' \
        f'|> range(start: time(v: "{start}"), stop: time(v: "{stop}")) ' \
        f'|> filter(fn: (r) => r._measurement == "meteredValues" and r._field == "value") ' \
        f'|> aggregateWindow(every: 6h, createEmpty: false, offset: {offset}, fn: sum)'

    logging.debug(
        f'Query get_bar_chart_values (weeks_back={str(weeks_back)}, date={str(date)}):\n{flux_query}')

    response = query_api.query(flux_query)
    if len(response) == 0 or len(response[0].records) == 0:
        logging.warning(f'No bar chart data between {start} and {stop}')
        return []

    values = list(map(lambda r: r.get_value(), response[0].records))

    return values


def get_ytd_statistics(date):
    # Convert to UTC as it's stored in DB
    start = pytz.timezone(tz).localize(
        datetime(date.year, 1, 1)).astimezone(pytz.UTC)
    stop = pytz.timezone(tz).localize(date).astimezone(pytz.UTC)
    flux_query_mean = f'from(bucket: "{bucket}") ' \
        f'|> range(start: time(v: "{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"), stop: time(v: "{stop.strftime("%Y-%m-%dT%H:%M:%SZ")}")) ' \
        f'|> filter(fn: (r) => r._measurement == "meteredValues" and r._field == "value") ' \
        f'|> aggregateWindow(every: 24h, createEmpty: false, fn: sum)' \
        f'|> mean()'
    response_mean = query_api.query(flux_query_mean)
    flux_query_sum = f'from(bucket: "{bucket}") ' \
        f'|> range(start: time(v: "{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"), stop: time(v: "{stop.strftime("%Y-%m-%dT%H:%M:%SZ")}")) ' \
        f'|> filter(fn: (r) => r._measurement == "meteredValues" and r._field == "value") ' \
        f'|> aggregateWindow(every: 24h, createEmpty: false, fn: sum)' \
        f'|> sum()'
    response_sum = query_api.query(flux_query_sum)

    logging.debug(
        f'Query get_ytd_statistics mean (date={str(date)}):\n{flux_query_mean}')
    logging.debug(
        f'Query get_ytd_statistics sum (date={str(date)}):\n{flux_query_sum}')

    return response_mean[0].records[0].get_value(), response_sum[0].records[0].get_value()


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
    has_data = len(values_w0) > 0 or len(values_w1) > 0 or len(values_w2) > 0

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

    os.makedirs('export', exist_ok=True)

    fig.suptitle('Stromverbrauch (kWh)' + title_suffix)
    # fig.tight_layout()

    # Kindle Paperwhite has native resolution of 1024 x 758 px @ 212 dpi
    dpi = 212
    # Some buffer needed to actually get 758w ...
    fig.set_size_inches(760 / dpi, 1024 / dpi)
    # Write to a temporary file and replace atomically: the Kindle never sees a
    # half-written image, and overwriting files in place can fail on synced
    # folders (iCloud Drive returns EDEADLK through a Docker bind mount).
    tmp_path = base_path + '.' + filename + '.tmp.png'
    plt.savefig(tmp_path, dpi=dpi)

    # Convert to greyscale supported by Kindle
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
