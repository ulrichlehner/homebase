import pytest

from homebase import hass


@pytest.mark.parametrize('url,expected', [
    ('http://homeassistant.local:8123', 'ws://homeassistant.local:8123/api/websocket'),
    ('https://ha.example.org/', 'wss://ha.example.org/api/websocket'),
    ('http://supervisor/core', 'ws://supervisor/core/api/websocket'),
    ('http://supervisor/core/websocket', 'ws://supervisor/core/websocket'),
])
def test_websocket_url(url, expected):
    assert hass.websocket_url(url) == expected
