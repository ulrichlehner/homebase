from datetime import date, timedelta
from zoneinfo import ZoneInfo

TZ = 'Europe/Vienna'


def test_reconstruct_helpers():
    import reconstruct
    m = reconstruct.FILE_RE.match('AT0000000000000000000000000000000_2023-03-06-2023-03-12.png')
    assert m and m.group('start') == '2023-03-06' and m.group('stop') == '2023-03-12'
    assert not reconstruct.FILE_RE.match('current.png')
    assert reconstruct.dst_switch_in_week(date(2023, 3, 24), TZ)      # week of 2023-03-26 switch
    assert not reconstruct.dst_switch_in_week(date(2023, 4, 5), TZ)
    ax = (100, 400, 50, 650)  # top, bottom, left, right
    assert reconstruct.px_to_kwh(ax, 400) == 0 and reconstruct.px_to_kwh(ax, 100) == reconstruct.Y_MAX
    assert abs(reconstruct.data_to_px_x(ax, 3) - 350) < 1e-9  # day 3 is centred


def test_find_charts_prefers_kept_originals(tmp_path):
    import reconstruct
    name = 'M_2023-03-06-2023-03-12.png'
    other = 'M_2023-03-13-2023-03-19.png'
    (tmp_path / 'archive').mkdir()
    (tmp_path / 'archive' / name).write_bytes(b'archived')
    (tmp_path / other).write_bytes(b'top')
    (tmp_path / 'current.png').write_bytes(b'x')
    (tmp_path / 'M_readings.csv').write_text('x')
    found = reconstruct.find_charts(tmp_path, 'M')
    assert [p.name for p in found] == [name, other]            # subfolders are searched, other files ignored
    (tmp_path / 'original_charts' / 'archive').mkdir(parents=True)
    (tmp_path / 'original_charts' / 'archive' / name).write_bytes(b'original')
    found = reconstruct.find_charts(tmp_path, 'M')
    assert [p.read_bytes() for p in found] == [b'original']    # kept originals are used exclusively


def test_render_all_replaces_in_place_and_keeps_originals(tmp_path, monkeypatch):
    import importlib
    from datetime import datetime
    monkeypatch.setenv('METER_ID', 'M')
    monkeypatch.setenv('TZ', TZ)
    monkeypatch.setenv('EXPORT_DIR', str(tmp_path))
    z = ZoneInfo(TZ)
    today = datetime.now(z).date()
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=3)
    lines = ['start_local;end_local;kwh;kw;source;method;note']
    t = datetime.combine(first_monday, datetime.min.time(), tzinfo=z)
    end = datetime.combine(this_monday, datetime.min.time(), tzinfo=z)
    while t < end:
        lines.append(f'{t.isoformat()};;0.100;0.400;portal;measured;n')
        t = (t.astimezone(ZoneInfo('UTC')) + timedelta(minutes=15)).astimezone(z)
    (tmp_path / 'M_all.csv').write_text('\n'.join(lines) + '\n')

    def name(weeks_ago):
        mon = this_monday - timedelta(weeks=weeks_ago)
        return f'M_{mon:%Y-%m-%d}-{mon + timedelta(days=6):%Y-%m-%d}.png'
    (tmp_path / 'archive').mkdir()
    (tmp_path / 'archive' / name(3)).write_bytes(b'old archived')   # oldest week lives in archive/
    (tmp_path / name(2)).write_bytes(b'old top level')              # middle week at the top level
    # newest finished week does not exist yet

    render = importlib.reload(__import__('render'))
    render.render_all()

    assert (tmp_path / 'archive' / name(3)).read_bytes() != b'old archived'
    assert not (tmp_path / name(3)).exists()                        # not duplicated at the top level
    assert (tmp_path / name(2)).read_bytes() != b'old top level'
    assert (tmp_path / name(1)).exists()                            # missing week created at the top level
    assert (tmp_path / 'current.png').exists()
    assert (tmp_path / 'original_charts' / 'archive' / name(3)).read_bytes() == b'old archived'
    assert (tmp_path / 'original_charts' / name(2)).read_bytes() == b'old top level'
    assert not (tmp_path / 'original_charts' / name(1)).exists()    # nothing to keep for a new chart
    render.render_all()                                              # second run must not touch the originals
    assert (tmp_path / 'original_charts' / name(2)).read_bytes() == b'old top level'
    assert not [p for p in tmp_path.rglob('*') if p.name.endswith('.tmp.png') or p.name.endswith('.tmp')]
