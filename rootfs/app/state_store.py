#!/usr/bin/env python3
"""
Persistent state for the add-on and the ESPHome secrets file.

The add-on used to write lights/controllers into /data/options.json. That file belongs to the
Supervisor: it is rewritten (and stripped down to the schema) whenever the user saves the add-on
options, which silently threw away every light. State now lives in /data/brmesh_state.json, and
options.json is only read.
"""
import copy
import json
import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

OPTIONS_PATH = '/data/options.json'
STATE_PATH = '/data/brmesh_state.json'
ESPHOME_DIR = '/config/esphome'
# ESPHome resolves `!secret` from secrets.yaml next to the device YAML, so this is the file that matters.
ESPHOME_SECRETS_PATH = os.path.join(ESPHOME_DIR, 'secrets.yaml')
HA_SECRETS_PATH = '/config/secrets.yaml'

# Secrets that older versions only wrote into Home Assistant's secrets.yaml.
MIGRATED_SECRETS = ('mesh_key', 'wifi_ssid', 'wifi_password', 'wifi_domain', 'api_encryption_key',
                    'ota_password', 'default_wifi_network')

DEFAULT_STATE: Dict[str, Any] = {
    'version': 2,
    'mesh_key': '',
    'lights': [],
    'controllers': [],
    'groups': [],
    'scenes': [],
    'ignored_devices': [],
    'settings': {
        'map_enabled': True,
        'latitude': 0.0,
        'longitude': 0.0,
        'zoom': 19,
        'generate_esphome_configs': True,
        'enable_nspanel_ui': False,
        'nspanel_entity_id': '',
        # BLE advertising parameters passed to the fastcon component
        'adv_interval_min': 0x20,
        'adv_interval_max': 0x28,
        'adv_duration': 40,
        'adv_repeats': 3,
        'include_all_lights_entity': True,
        'enable_web_server': False,
        'board': 'esp32dev',
    },
}


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class StateStore:
    def __init__(self, path: str = None):
        self.path = path or STATE_PATH
        self.lock = threading.RLock()
        self.options = self._read_json(OPTIONS_PATH) or {}
        self.data = self._load()

    @staticmethod
    def _read_json(path: str):
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Could not read {path}: {e}")
            return None

    def _load(self) -> Dict[str, Any]:
        state = copy.deepcopy(DEFAULT_STATE)
        stored = self._read_json(self.path)
        if stored is None:
            stored = self._migrate_from_options()
        for key, value in stored.items():
            if key == 'settings' and isinstance(value, dict):
                state['settings'].update(value)
            else:
                state[key] = value
        state['lights'] = [l for l in (self._normalize_light(l) for l in state.get('lights') or []) if l]
        return state

    def _migrate_from_options(self) -> Dict[str, Any]:
        """Pick up lights/controllers that older versions stored in options.json."""
        old = self.options
        migrated: Dict[str, Any] = {}
        for key in ('mesh_key', 'controllers', 'scenes', 'ignored_devices'):
            if old.get(key):
                migrated[key] = old[key]
        lights = old.get('lights')
        if isinstance(lights, dict):  # EXAMPLE_CONFIG.json style {"1": {...}}
            lights = [dict(v, light_id=int(k)) for k, v in lights.items()]
        if lights:
            migrated['lights'] = lights
        settings = {}
        for old_key, new_key in (('map_enabled', 'map_enabled'), ('latitude', 'latitude'), ('longitude', 'longitude'),
                                 ('map_latitude', 'latitude'), ('map_longitude', 'longitude'), ('zoom', 'zoom'),
                                 ('generate_esphome_configs', 'generate_esphome_configs'),
                                 ('enable_nspanel_ui', 'enable_nspanel_ui'), ('nspanel_entity_id', 'nspanel_entity_id')):
            if old_key in old and old[old_key] not in (None, ''):
                settings[new_key] = old[old_key]
        if settings:
            migrated['settings'] = settings
        if migrated:
            logger.info(f"Migrated {len(migrated.get('lights', []))} lights and "
                        f"{len(migrated.get('controllers', []))} controllers from options.json")
        return migrated

    @staticmethod
    def _normalize_light(light: Dict) -> Dict:
        """Accept the different shapes older versions wrote (light_id / address / id)."""
        if not isinstance(light, dict):
            return None
        light_id = light.get('light_id', light.get('address', light.get('id')))
        try:
            light_id = int(light_id)
        except (TypeError, ValueError):
            return None
        if not 1 <= light_id <= 255:
            return None
        out = dict(light)
        out['light_id'] = light_id
        out.setdefault('name', f'BRMesh Light {light_id}')
        out.setdefault('supports_cwww', False)
        out.setdefault('color_interlock', True)
        out.setdefault('location', {'x': None, 'y': None})
        for stale in ('address', 'id', 'state', 'signal_strength', 'paired'):
            out.pop(stale, None)
        return out

    def save(self) -> None:
        with self.lock:
            try:
                _atomic_write(self.path, json.dumps(self.data, indent=2))
            except Exception as e:
                logger.error(f"Failed to save state: {e}")

    def option(self, key: str, default: Any = None) -> Any:
        """Read-only add-on option from the Supervisor (options.json)."""
        return self.options.get(key, default)

    # --- ESPHome secrets -------------------------------------------------

    @staticmethod
    def _yaml():
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.default_flow_style = False
        return yaml

    def read_secrets(self, path: str = None) -> Dict[str, Any]:
        path = path or ESPHOME_SECRETS_PATH
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                return self._yaml().load(f) or {}
        except Exception as e:
            logger.error(f"Could not read {path}: {e}")
            return {}

    def update_secrets(self, values: Dict[str, Any], remove: tuple = ()) -> None:
        """Set keys in /config/esphome/secrets.yaml, keeping comments and other keys."""
        from io import StringIO
        from ruamel.yaml.comments import CommentedMap
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        with self.lock:
            os.makedirs(os.path.dirname(ESPHOME_SECRETS_PATH), exist_ok=True)
            secrets = self.read_secrets()
            if not isinstance(secrets, CommentedMap):
                secrets = CommentedMap(secrets)
            for key in remove:
                secrets.pop(key, None)
            for key, value in values.items():
                # Quote strings so hex keys like "30323336" stay strings, not numbers.
                secrets[key] = DoubleQuotedScalarString(str(value)) if isinstance(value, str) else value
            buf = StringIO()
            self._yaml().dump(secrets, buf)
            _atomic_write(ESPHOME_SECRETS_PATH, buf.getvalue())

    def migrate_secrets(self) -> None:
        """Copy secrets that older versions only wrote to Home Assistant's secrets.yaml."""
        ha = self.read_secrets(HA_SECRETS_PATH) if os.path.abspath(HA_SECRETS_PATH) != os.path.abspath(ESPHOME_SECRETS_PATH) else {}
        if not ha:
            return
        current = self.read_secrets()
        missing = {k: v for k, v in ha.items()
                   if (k in MIGRATED_SECRETS or str(k).startswith('wifi_network_')) and k not in current}
        if missing:
            self.update_secrets({k: (str(v) if not isinstance(v, (int, bool)) else v) for k, v in missing.items()})
            logger.info(f"Copied {len(missing)} secrets from /config/secrets.yaml to {ESPHOME_SECRETS_PATH}")
