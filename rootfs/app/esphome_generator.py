#!/usr/bin/env python3
"""
ESPHome configuration generator for BRMesh controllers.

Every controller gets every light: the lamps form a mesh and relay commands, so any ESP32 in range
of one lamp can reach all of them.
"""
import base64
import logging
import os
import secrets as pysecrets
import shutil
import string
from typing import Dict, List

import yaml

from state_store import ESPHOME_DIR

logger = logging.getLogger(__name__)

BRIDGE_FIRMWARE_VERSION = "2.1.0"
COMPONENTS_SRC = '/app/esphome_components'
# Folder (relative to /config/esphome) the bundled ESPHome components are copied to.
COMPONENTS_DIR_NAME = 'esp_ble_bridge_components'
MANUAL_MARKERS = ("# manual_config: true", "# manual_managed: true")
ALL_LIGHTS_NAME = 'All Lights'
# Entity name of the controller's pairing status text sensor (fastcon pairing_status).
PAIRING_SENSOR_NAME = 'Pairing'
# Each light entity costs ~350 bytes of heap (LightState 248 + FastconLight 28 bytes, measured from
# the firmware's debug info, plus name and allocator overhead). A classic ESP32 has roughly 60-90 KB
# free with Wi-Fi, Bluetooth and the API running, so above this many lights per controller, suggest
# spreading them over more controllers.
LIGHTS_PER_CONTROLLER_WARNING = 100


class Secret(str):
    """A value written as `!secret name`."""


class _Dumper(yaml.SafeDumper):
    pass


def _secret_representer(dumper, data):
    return dumper.represent_scalar('!secret', str(data))


_Dumper.add_representer(Secret, _secret_representer)


def controller_slug(name: str) -> str:
    return name.lower().strip().replace(' ', '-').replace('_', '-')


class ESPHomeConfigGenerator:
    def __init__(self, bridge):
        self.bridge = bridge
        self.config_dir = ESPHOME_DIR
        os.makedirs(self.config_dir, exist_ok=True)

    # --- bundled components ------------------------------------------------

    def install_components(self) -> None:
        """Copy the bundled fastcon components next to the device YAMLs.

        The ESPHome add-on compiles in its own container and only shares /config, so the components
        have to live there. Using a local copy also removes the GitHub clone from every build.
        """
        dest = os.path.join(self.config_dir, COMPONENTS_DIR_NAME)
        if not os.path.isdir(COMPONENTS_SRC):
            logger.warning(f"Bundled components not found at {COMPONENTS_SRC}")
            return
        tmp = dest + '.new'
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(COMPONENTS_SRC, tmp)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        logger.info(f"Installed ESPHome components to {dest}")

    # --- YAML generation -----------------------------------------------------

    def generate_controller_config(self, controller: Dict) -> str:
        settings = self.bridge.settings
        name = controller_slug(controller['name'])

        wifi = {
            'ssid': Secret('wifi_ssid'),
            'password': Secret('wifi_password'),
            'ap': {'ssid': f'{name[:24]}-fallback', 'password': Secret('ota_password')},
        }
        if self.bridge.store.option('wifi_domain'):
            wifi['domain'] = Secret('wifi_domain')
        if controller.get('ip_address'):
            wifi['use_address'] = controller['ip_address']

        config: Dict = {
            'esphome': {
                'name': name,
                'friendly_name': controller['name'],
                'comment': f'ESP BLE Bridge v{BRIDGE_FIRMWARE_VERSION}',
                'project': {'name': 'esp_ble_bridge.brmesh', 'version': BRIDGE_FIRMWARE_VERSION},
            },
            'esp32': {
                'board': controller.get('board') or settings.get('board', 'esp32dev'),
                # ESP-IDF has the better Wi-Fi/BLE coexistence and leaves more RAM than Arduino.
                'framework': {'type': 'esp-idf'},
            },
            # DEBUG logging of every packet slows the loop that sends the BLE commands.
            'logger': {'level': 'INFO'},
            'api': {
                'encryption': {'key': Secret('api_encryption_key')},
                # Called by the add-on through Home Assistant (esphome.<device>_start_pairing).
                'actions': [
                    {'action': 'start_pairing',
                     'variables': {'light_ids': 'int[]', 'seconds': 'int'},
                     'then': [{'lambda': 'id(fastcon_controller).start_pairing(light_ids, seconds);'}]},
                    {'action': 'stop_pairing',
                     'then': [{'lambda': 'id(fastcon_controller).stop_pairing();'}]},
                ],
            },
            'ota': [{'platform': 'esphome', 'password': Secret('ota_password')}],
            'wifi': wifi,
            'captive_portal': {},
            'external_components': [{
                'source': {'type': 'local', 'path': COMPONENTS_DIR_NAME},
                'components': ['fastcon', 'fastcon_broadcast_light', 'fastcon_group_light'],
            }],
            # No esp32_ble_tracker / esp32_ble_server: scanning shares the single radio with the
            # advertisements that carry the commands, and was the main reason lamps missed them.
            'fastcon': {
                'id': 'fastcon_controller',
                'mesh_key': Secret('mesh_key'),
                'adv_interval_min': int(settings.get('adv_interval_min', 0x20)),
                'adv_interval_max': int(settings.get('adv_interval_max', 0x28)),
                'adv_duration': int(settings.get('adv_duration', 40)),
                'repeats': int(settings.get('adv_repeats', 3)),
                'pairing_status': {'name': PAIRING_SENSOR_NAME},
            },
        }
        if settings.get('enable_web_server'):
            config['web_server'] = {'port': 80, 'version': 3}

        lights: List[Dict] = []
        for light, name in self.controller_lights(controller):
            kind = light.get('kind') or ('rgbcw' if light.get('supports_cwww') else 'rgbw')
            entry = {
                'platform': 'fastcon',
                'id': f"brmesh_light_{light['light_id']}",
                'name': name,
                'light_id': light['light_id'],
                'light_type': kind,
            }
            if kind in ('rgbcw', 'rgbw', 'rgb'):
                entry['supports_cwww'] = bool(light.get('supports_cwww', kind == 'rgbcw'))
                entry['color_interlock'] = bool(light.get('color_interlock', True))
            # A transition makes ESPHome rewrite the light every loop; each write is a BLE packet.
            entry['default_transition_length'] = '0s'
            entry['gamma_correct'] = 1.0
            lights.append(entry)
        if len(lights) > 1 and settings.get('include_all_lights_entity', True):
            lights.append({
                'platform': 'fastcon_broadcast_light',
                'id': 'brmesh_all_lights',
                'name': ALL_LIGHTS_NAME,
                'mesh_key': Secret('mesh_key'),
                'default_transition_length': '0s',
                'gamma_correct': 1.0,
            })
        config['light'] = lights

        config['sensor'] = [
            {'platform': 'wifi_signal', 'name': 'WiFi Signal', 'update_interval': '60s'},
            {'platform': 'uptime', 'name': 'Uptime', 'update_interval': '60s'},
        ]
        config['binary_sensor'] = [{'platform': 'status', 'name': 'Status'}]
        config['text_sensor'] = [
            {'platform': 'wifi_info', 'ip_address': {'name': 'IP Address'}},
            {'platform': 'version', 'name': 'ESPHome Version'},
        ]
        config['button'] = [
            {'platform': 'restart', 'name': 'Restart'},
            {'platform': 'safe_mode', 'name': 'Safe Mode Boot'},
        ]

        header = (f"# Generated by the ESP BLE Bridge add-on (firmware {BRIDGE_FIRMWARE_VERSION}).\n"
                  f"# Regenerated when lights change. To manage this file yourself, add a line that reads\n"
                  f"# exactly \"# manual_config: true\" (without the quotes).\n")
        body = yaml.dump(config, Dumper=_Dumper, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return header + body

    def controller_lights(self, controller: Dict) -> List[tuple]:
        """(light, entity name) pairs for a controller, with names made unique.

        A controller gets every light unless the user picked a subset (`light_ids`). ESPHome rejects
        two entities with the same name, and app exports often reuse names, so duplicates get their ID.
        """
        selected = controller.get('light_ids')
        lights = [l for _, l in sorted(self.bridge.lights.items())
                  if selected is None or l['light_id'] in set(selected)]
        counts: Dict[str, int] = {}
        for l in lights:
            key = l['name'].strip().lower()
            counts[key] = counts.get(key, 0) + 1
        result = []
        for l in lights:
            name = l['name'].strip() or f"BRMesh Light {l['light_id']}"
            if counts[name.lower()] > 1 or name.lower() == ALL_LIGHTS_NAME.lower():
                name = f"{name} ({l['light_id']})"
            result.append((l, name))
        return result

    @staticmethod
    def is_manual(content: str) -> bool:
        return any(line.strip() in MANUAL_MARKERS for line in content.splitlines())

    def config_path(self, controller: Dict) -> str:
        return os.path.join(self.config_dir, f"{controller_slug(controller['name'])}.yaml")

    def generate_all_configs(self, force: bool = False) -> Dict[str, Dict]:
        """Write the YAML for every controller.

        Without `force`, existing files are only reported as having an update available.
        Files containing a manual_config marker are never touched.
        """
        results = {}
        for controller in self.bridge.controllers:
            path = self.config_path(controller)
            result = {'path': path}
            try:
                content = self.generate_controller_config(controller)
                existing = None
                if os.path.exists(path):
                    with open(path) as f:
                        existing = f.read()
                if existing is not None and self.is_manual(existing):
                    result['status'] = 'manual_override'
                elif existing == content:
                    result['status'] = 'skipped'
                elif existing is not None and not force:
                    result['status'] = 'update_available'
                else:
                    with open(path, 'w') as f:
                        f.write(content)
                    result['status'] = 'updated' if existing is not None else 'created'
                    logger.info(f"{result['status'].capitalize()} ESPHome config {path}")
            except Exception as e:
                logger.error(f"Failed to write config {path}: {e}")
                result['status'] = 'error'
                result['error'] = str(e)
            results[controller['name']] = result
        return results

    # --- secrets ---------------------------------------------------------------

    def ensure_secrets(self) -> None:
        """Make sure every secret the generated YAML references exists."""
        current = self.bridge.store.read_secrets()
        updates = {}
        if not self._is_valid_api_key(str(current.get('api_encryption_key', ''))):
            updates['api_encryption_key'] = base64.b64encode(pysecrets.token_bytes(32)).decode('ascii')
        ota = str(current.get('ota_password', ''))
        if len(ota) < 8 or ota == 'your_ota_password':
            alphabet = string.ascii_letters + string.digits
            updates['ota_password'] = ''.join(pysecrets.choice(alphabet) for _ in range(16))
        if self.bridge.mesh_key and str(current.get('mesh_key', '')).lower() != self.bridge.mesh_key:
            updates['mesh_key'] = self.bridge.mesh_key
        domain = self.bridge.store.option('wifi_domain')
        if domain and current.get('wifi_domain') != domain:
            updates['wifi_domain'] = domain
        if updates:
            self.bridge.store.update_secrets(updates)
            logger.info(f"Updated secrets: {', '.join(sorted(updates))}")

    @staticmethod
    def _is_valid_api_key(key: str) -> bool:
        try:
            return len(base64.b64decode(key, validate=True)) == 32
        except Exception:
            return False

    def sync_configs(self) -> Dict[str, Dict]:
        if not self.bridge.settings.get('generate_esphome_configs', True):
            return {}
        self.ensure_secrets()
        results = self.generate_all_configs(force=False)
        available = [n for n, r in results.items() if r['status'] == 'update_available']
        if available:
            logger.info(f"ESPHome config updates available for {', '.join(available)} (regenerate from the Web UI)")
        return results
