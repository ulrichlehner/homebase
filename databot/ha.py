"""Minimal Home Assistant websocket client for statistics and history.

Used by the fusion add-on (through the Supervisor proxy) and by ha_spike.py (with a
long-lived access token from the Mac). Only the few commands the project needs:

  recorder/import_statistics          write external statistics (whole hours, UTC)
  recorder/statistics_during_period   read statistics back
  recorder/get_statistics_metadata    find statistics by ID
  recorder/clear_statistics           delete statistics
  history/history_during_period       raw states of an entity (the RF counter)
  call_service                        notifications
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import websocket

logger = logging.getLogger('ha')

UTC = ZoneInfo('UTC')


class HomeAssistantError(RuntimeError):
    """A websocket command failed or the connection was refused."""


class AuthError(HomeAssistantError):
    """The access token was rejected."""


def websocket_url(base_url: str) -> str:
    """http://host:8123 -> ws://host:8123/api/websocket (https -> wss)."""
    u = urlparse(base_url.rstrip('/'))
    scheme = 'wss' if u.scheme == 'https' else 'ws'
    path = u.path if u.path.endswith('/websocket') else u.path + '/api/websocket'
    return f'{scheme}://{u.netloc}{path}'


class HomeAssistant:
    """Synchronous websocket session. Use as a context manager."""

    def __init__(self, url: str, token: str, timeout: float = 60.0):
        self.url = websocket_url(url) if url.startswith('http') else url
        self.token = token
        self.timeout = timeout
        self.version: Optional[str] = None
        self._ws: Optional[websocket.WebSocket] = None
        self._id = 0

    def __enter__(self) -> 'HomeAssistant':
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        except (OSError, websocket.WebSocketException) as err:
            raise HomeAssistantError(f'cannot connect to {self.url}: {err}') from err
        hello = self._recv()
        if hello.get('type') != 'auth_required':
            raise HomeAssistantError(f'unexpected greeting {hello}')
        self.version = hello.get('ha_version')
        self._send({'type': 'auth', 'access_token': self.token})
        reply = self._recv()
        if reply.get('type') != 'auth_ok':
            raise AuthError(reply.get('message', 'authentication failed'))
        logger.debug('Connected to Home Assistant %s', self.version)

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def _send(self, payload: dict) -> None:
        self._ws.send(json.dumps(payload))

    def _recv(self) -> dict:
        return json.loads(self._ws.recv())

    def call(self, type_: str, **payload: Any) -> Any:
        """Sends one command and returns its result; raises on an error reply."""
        self._id += 1
        self._send({'id': self._id, 'type': type_, **payload})
        while True:
            reply = self._recv()
            if reply.get('id') != self._id:
                continue   # events of earlier subscriptions, if any
            if reply.get('type') == 'result' and not reply.get('success'):
                err = reply.get('error', {})
                raise HomeAssistantError(f'{type_}: {err.get("code")}: {err.get("message")}')
            return reply.get('result')

    def call_service(self, domain: str, service: str, data: Optional[dict] = None) -> Any:
        return self.call('call_service', domain=domain, service=service, service_data=data or {})

    # -- Statistics ------------------------------------------------------------

    def import_statistics(self, metadata: dict, stats: list) -> None:
        """stats: [{'start': aware datetime on a full hour, 'sum': float, ...}]."""
        rows = [{**s, 'start': s['start'].isoformat()} for s in stats]
        self.call('recorder/import_statistics', metadata=metadata, stats=rows)

    def statistics_during_period(self, statistic_ids: list, start: datetime, end: Optional[datetime] = None,
                                 period: str = 'hour', types: tuple = ('sum', 'state', 'change')) -> dict:
        payload: dict = {'statistic_ids': statistic_ids, 'start_time': start.isoformat(), 'period': period,
                         'types': list(types)}
        if end:
            payload['end_time'] = end.isoformat()
        return self.call('recorder/statistics_during_period', **payload) or {}

    def statistics_metadata(self, statistic_ids: list) -> list:
        return self.call('recorder/get_statistics_metadata', statistic_ids=statistic_ids) or []

    def clear_statistics(self, statistic_ids: list) -> None:
        self.call('recorder/clear_statistics', statistic_ids=statistic_ids)

    # -- History -----------------------------------------------------------------

    def history(self, entity_id: str, start: datetime, end: Optional[datetime] = None) -> list:
        """[(UTC datetime, state string)] of one entity, oldest first."""
        payload: dict = {'entity_ids': [entity_id], 'start_time': start.isoformat(),
                         'minimal_response': True, 'no_attributes': True}
        if end:
            payload['end_time'] = end.isoformat()
        result = self.call('history/history_during_period', **payload) or {}
        out = []
        for s in result.get(entity_id, []):
            # compressed format: 's' state, 'lu' last updated, 'lc' last changed (epoch seconds)
            ts = s.get('lc', s.get('lu'))
            if ts is None or 's' not in s:
                continue
            out.append((datetime.fromtimestamp(ts, UTC), s['s']))
        return out

