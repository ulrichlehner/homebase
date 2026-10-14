"""Status entities of the add-on in Home Assistant, through MQTT discovery.

One device "LINZ NETZ Energy <site>" with a few entities for dashboards and automations:

  sensor        last_portal_fetch      when the portal was fetched successfully the last time
  sensor        portal_data_until      the newest portal reading
  sensor        live_data_until          the newest hour with a value of the live counter
  sensor        live_deviation           the live counter against the portal on the last complete day, in %
  sensor        deviating_hours        live hours of the last 7 days that differ from the portal
  binary_sensor portal_login_problem   on while the portal rejects the login

The broker comes from the Supervisor (`services: mqtt:want`): no credentials to enter. Without an
MQTT broker the add-on runs as before, only without these entities. Discovery configs and the state
are retained, so the entities survive restarts of Home Assistant and of the add-on.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

from texts import t

logger = logging.getLogger('addon.mqtt')

DISCOVERY_PREFIX = 'homeassistant'
SUPERVISOR_MQTT = 'http://supervisor/services/mqtt'

# key: (component, device class, unit, state class, diagnostic, icon)
ENTITIES = {
    'last_portal_fetch': ('sensor', 'timestamp', None, None, True, None),
    'portal_data_until': ('sensor', 'timestamp', None, None, False, None),
    'live_data_until': ('sensor', 'timestamp', None, None, False, 'mdi:meter-electric'),
    'live_deviation': ('sensor', None, '%', 'measurement', False, 'mdi:compare-horizontal'),
    'deviating_hours': ('sensor', None, None, 'measurement', False, 'mdi:alert-circle-outline'),
    'portal_login_problem': ('binary_sensor', 'problem', None, None, True, None),
}


def broker() -> Optional[dict]:
    """The MQTT broker from the Supervisor ({host, port, username, password, ssl}), or from the
    variables MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD for a test outside Home
    Assistant; None if there is none."""
    if os.environ.get('MQTT_HOST'):
        return {'host': os.environ['MQTT_HOST'], 'port': int(os.environ.get('MQTT_PORT') or 1883),
                'username': os.environ.get('MQTT_USERNAME'), 'password': os.environ.get('MQTT_PASSWORD'),
                'ssl': False}
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        return None
    try:
        r = requests.get(SUPERVISOR_MQTT, headers={'Authorization': f'Bearer {token}'}, timeout=10)
        if r.status_code != 200:
            return None
        return r.json().get('data') or None
    except (requests.RequestException, ValueError):
        return None


def messages(site: str, site_id: str, lang: str, version: str, values: dict) -> list:
    """[(topic, payload)] to publish: one discovery config per entity and the shared state."""
    node = f'linznetz_energy_{site_id}'
    state_topic = f'linznetz_energy/{site_id}/state'
    device = {'identifiers': [node], 'name': f'LINZ NETZ Energy {site}', 'manufacturer': 'homebase',
              'model': 'LINZ NETZ Energy add-on', 'sw_version': version}
    out = []
    for key, (component, device_class, unit, state_class, diagnostic, icon) in ENTITIES.items():
        config = {
            'name': t(lang, f'entity.{key}'),
            'unique_id': f'{node}_{key}',
            'default_entity_id': f'{component}.{site_id}_linznetz_{key}',
            'state_topic': state_topic,
            'device': device,
        }
        if component == 'binary_sensor':
            config['value_template'] = f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}"
        else:
            # JSON null renders as "None", which MQTT sensors show as unknown
            config['value_template'] = f'{{{{ value_json.{key} }}}}'
        for field, value in (('device_class', device_class), ('unit_of_measurement', unit),
                             ('state_class', state_class), ('icon', icon)):
            if value:
                config[field] = value
        if diagnostic:
            config['entity_category'] = 'diagnostic'
        out.append((f'{DISCOVERY_PREFIX}/{component}/{node}/{key}/config', json.dumps(config)))
    out.append((state_topic, json.dumps({k: values.get(k) for k in ENTITIES})))
    return out


def publish(connection: dict, site: str, site_id: str, lang: str, version: str, values: dict) -> bool:
    """Publishes discovery and state (retained). Returns False when the broker cannot be reached;
    the add-on goes on without the entities."""
    import paho.mqtt.client as mqtt   # only in the add-on image

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'linznetz_energy_{site_id}')
    if connection.get('username'):
        client.username_pw_set(connection['username'], connection.get('password'))
    if connection.get('ssl'):
        client.tls_set()
    try:
        client.connect(connection['host'], int(connection.get('port') or 1883), keepalive=30)
        client.loop_start()
        for topic, payload in messages(site, site_id, lang, version, values):
            client.publish(topic, payload, qos=1, retain=True).wait_for_publish(timeout=10)
        return True
    except (OSError, ValueError, RuntimeError) as err:
        logger.warning('MQTT broker %s not reachable, no status entities this time: %s', connection.get('host'), err)
        return False
    finally:
        client.loop_stop()
        client.disconnect()
