import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import ha_spike
from homebase import hass as ha

ZONE = ZoneInfo('Europe/Vienna')
UTC = ZoneInfo('UTC')


class FakeHass:
    """Accepts an import only once the metadata has every field in `required`."""

    def __init__(self, required):
        self.required = required
        self.calls = []

    def import_statistics(self, metadata, stats):
        self.calls.append(dict(metadata))
        for key in self.required:
            if key not in metadata:
                raise ha.HomeAssistantError(f'recorder/import_statistics: invalid_format: required key not provided @ data[\'metadata\'][\'{key}\']')


@pytest.mark.parametrize('required,calls', [((), 1), (('mean_type',), 2), (('mean_type', 'unit_class'), 3)])
def test_metadata_discovery(required, calls):
    fake = FakeHass(required)
    metadata = ha_spike.import_with_discovery(fake, [])
    assert len(fake.calls) == calls
    assert all(k in metadata for k in required)


def test_metadata_discovery_passes_other_errors_on():
    class Broken:
        def import_statistics(self, metadata, stats):
            raise ha.HomeAssistantError('recorder/import_statistics: unauthorized')
    with pytest.raises(ha.HomeAssistantError, match='unauthorized'):
        ha_spike.import_with_discovery(Broken(), [])


def test_week_of_hours_takes_complete_portal_days(tmp_path):
    path = tmp_path / 'fused.csv'
    start = datetime(2026, 10, 1, tzinfo=ZONE).astimezone(UTC)
    with path.open('w', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['start_local', 'start_utc', 'kwh', 'source', 'sum_kwh'])
        for i in range(24 * 10):
            h = start + timedelta(hours=i)
            source = 'rf' if i >= 24 * 9 + 5 else 'portal'   # day 10 is partly RF
            w.writerow([h.astimezone(ZONE).isoformat(), h.strftime('%Y-%m-%dT%H:%M:%SZ'), '0.500', source, ''])
    hours = ha_spike.week_of_hours(path, ZONE)
    assert len(hours) == 7 * 24
    assert hours[0][0] == start + timedelta(days=2) and hours[-1][0] == start + timedelta(days=9) - timedelta(hours=1)
    stats = ha_spike.to_stats(hours)
    assert stats[-1]['sum'] == 84.0 and stats[0]['start'].minute == 0
