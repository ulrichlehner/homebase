from datetime import date

import databot
from homebase import disaggregate, readings


def test_cli_defaults():
    args = databot.build_parser().parse_args([])
    assert args.command in (None, 'csv') and args.overlap == readings.DEFAULT_OVERLAP_DAYS
    args = databot.build_parser().parse_args(['csv', '--overlap', '30', '--stop-after-empty', '3'])
    assert args.overlap == 30 and args.stop_after_empty == 3
    assert databot.build_parser().parse_args(['check']).command == 'check'


def test_cli_subcommands():
    p = databot.build_parser()
    args = p.parse_args(['disaggregate', '--backtest', '--backtest-from', '2025-03-01'])
    assert args.command == 'disaggregate' and args.backtest and args.backtest_from == date(2025, 3, 1)
    assert args.profile_from == disaggregate.DEFAULT_PROFILE_FROM
    args = p.parse_args(['fuse', '--live', 'h.csv', '--max-gap-minutes', '30'])
    assert args.command == 'fuse' and args.live == 'h.csv' and args.max_gap_minutes == 30


def test_login_is_only_needed_for_csv(monkeypatch):
    for k in ('USERNAME', 'PASSWORD'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('METER_ID', 'M')
    monkeypatch.setenv('TZ', 'Europe/Vienna')
    assert databot.load_config(None, need_login=False).meter_id == 'M'
    try:
        databot.load_config(None, need_login=True)
    except SystemExit as err:
        assert 'USERNAME' in str(err)
    else:
        raise AssertionError('missing login not reported')


def test_old_rf_options_still_work():
    args = databot.build_parser().parse_args(['fuse', '--rf', 'h.csv', '--rf-entity', 'sensor.x'])
    assert args.live == 'h.csv' and args.live_meter_entity == 'sensor.x'
