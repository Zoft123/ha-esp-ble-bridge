"""Writes the ESPHome YAML of each ESP32 controller.

A controller gets every light unless the user picked a subset: the lamps form a mesh and relay
commands, so an ESP32 in range of one lamp reaches all of them.
"""
import base64
import os
import secrets as pysecrets
import shutil
import string
from logging import getLogger
from typing import Dict, List, Tuple

import yaml

from state_store import ESPHOME_DIR

log = getLogger(__name__)

BRIDGE_FIRMWARE_VERSION = "2.1.0"
COMPONENTS_SRC = '/app/esphome_components'
# Folder, inside /config/esphome, that the bundled ESPHome components are copied to.
COMPONENTS_DIR_NAME = 'esp_ble_bridge_components'
# A YAML containing this line is left alone (the user manages it).
MANUAL_MARKER = '# manual_config: true'
ALL_LIGHTS_NAME = 'All Lights'
# Entity name of the controller's pairing status text sensor (fastcon pairing_status).
PAIRING_SENSOR_NAME = 'Pairing'
# Each light entity costs ~350 bytes of heap (LightState 248 + FastconLight 28 bytes, measured from
# the firmware's debug info, plus name and allocator overhead). A classic ESP32 has roughly 60-90 KB
# free with Wi-Fi, Bluetooth and the API running, so above this many lights per controller, suggest
# spreading them over more controllers.
LIGHTS_PER_CONTROLLER_WARNING = 100
# Lamp kinds with color LEDs (they may also have cold/warm white).
COLOR_KINDS = ('rgbcw', 'rgbw', 'rgb')


class Secret(str):
    """Written to the YAML as `!secret <name>`."""


class _YamlWriter(yaml.SafeDumper):
    """SafeDumper that knows Secret."""


_YamlWriter.add_representer(Secret, lambda dumper, value: dumper.represent_scalar('!secret', str(value)))


def controller_slug(name: str) -> str:
    """ESPHome node name of a controller (also its YAML file name and URL key)."""
    return name.strip().lower().replace('_', '-').replace(' ', '-')


class ESPHomeConfigGenerator:
    def __init__(self, bridge):
        self.br = bridge
        self.config_dir = ESPHOME_DIR
        os.makedirs(ESPHOME_DIR, exist_ok=True)

    @property
    def bridge(self):
        return self.br

    @bridge.setter
    def bridge(self, value):
        self.br = value

    # --- bundled components --------------------------------------------------------------------

    def install_components(self) -> None:
        """Copy the bundled fastcon components next to the device YAMLs.

        The ESPHome add-on compiles in its own container and only shares /config, so the components
        have to live there. Using a local copy also removes the GitHub clone from every build.
        """
        if not os.path.isdir(COMPONENTS_SRC):
            log.warning(f"Bundled components missing at {COMPONENTS_SRC}")
            return
        target = os.path.join(self.config_dir, COMPONENTS_DIR_NAME)
        staging = f'{target}.new'
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(COMPONENTS_SRC, staging)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        log.info(f"ESPHome components installed to {target}")

    # --- YAML ------------------------------------------------------------------------------------

    def _base(self, controller: Dict, node: str) -> Dict:
        s = self.br.settings
        wifi = {
            'ssid': Secret('wifi_ssid'),
            'password': Secret('wifi_password'),
            'ap': {'ssid': f'{node[:24]}-fallback', 'password': Secret('ota_password')},
        }
        if self.br.store.option('wifi_domain'):
            wifi['domain'] = Secret('wifi_domain')
        if controller.get('ip_address'):
            wifi['use_address'] = controller['ip_address']
        pairing_actions = [
            {'action': 'start_pairing',
             'variables': {'light_ids': 'int[]', 'seconds': 'int'},
             'then': [{'lambda': 'id(fastcon_controller).start_pairing(light_ids, seconds);'}]},
            {'action': 'stop_pairing',
             'then': [{'lambda': 'id(fastcon_controller).stop_pairing();'}]},
        ]
        return {
            'esphome': {
                'name': node,
                'friendly_name': controller['name'],
                'comment': f'ESP BLE Bridge v{BRIDGE_FIRMWARE_VERSION}',
                'project': {'name': 'esp_ble_bridge.brmesh', 'version': BRIDGE_FIRMWARE_VERSION},
            },
            # ESP-IDF has the better Wi-Fi/BLE coexistence and leaves more RAM than Arduino.
            'esp32': {'board': controller.get('board') or s.get('board', 'esp32dev'), 'framework': {'type': 'esp-idf'}},
            # DEBUG logging of every packet slows the loop that sends the BLE commands.
            'logger': {'level': 'INFO'},
            # The actions are called by the add-on through Home Assistant (esphome.<node>_start_pairing).
            'api': {'encryption': {'key': Secret('api_encryption_key')}, 'actions': pairing_actions},
            'ota': [{'platform': 'esphome', 'password': Secret('ota_password')}],
            'wifi': wifi,
            'captive_portal': {},
            'external_components': [{
                'source': {'type': 'local', 'path': COMPONENTS_DIR_NAME},
                'components': ['fastcon', 'fastcon_broadcast_light', 'fastcon_group_light'],
            }],
            # No esp32_ble_tracker / esp32_ble_server: they would share the radio with the advertisements
            # that carry the commands. The fastcon controller scans by itself, only while pairing.
            'fastcon': {
                'id': 'fastcon_controller',
                'mesh_key': Secret('mesh_key'),
                'adv_interval_min': int(s.get('adv_interval_min', 0x20)),
                'adv_interval_max': int(s.get('adv_interval_max', 0x28)),
                'adv_duration': int(s.get('adv_duration', 40)),
                'repeats': int(s.get('adv_repeats', 3)),
                'pairing_status': {'name': PAIRING_SENSOR_NAME},
            },
        }

    def _light_entries(self, controller: Dict) -> List[Dict]:
        entries = []
        for light, entity_name in self.controller_lights(controller):
            kind = light.get('kind') or ('rgbcw' if light.get('supports_cwww') else 'rgbw')
            entry = {
                'platform': 'fastcon',
                'id': f"brmesh_light_{light['light_id']}",
                'name': entity_name,
                'light_id': light['light_id'],
                'light_type': kind,
            }
            if kind in COLOR_KINDS:
                entry['supports_cwww'] = bool(light.get('supports_cwww', kind == 'rgbcw'))
                entry['color_interlock'] = bool(light.get('color_interlock', True))
            # With a transition ESPHome rewrites the light every loop, and every write is a BLE packet.
            entry.update(default_transition_length='0s', gamma_correct=1.0)
            entries.append(entry)
        if len(entries) > 1 and self.br.settings.get('include_all_lights_entity', True):
            entries.append({
                'platform': 'fastcon_broadcast_light',
                'id': 'brmesh_all_lights',
                'name': ALL_LIGHTS_NAME,
                'mesh_key': Secret('mesh_key'),
                'default_transition_length': '0s',
                'gamma_correct': 1.0,
            })
        return entries

    @staticmethod
    def _diagnostics() -> Dict:
        """Entities that show the controller's health in Home Assistant."""
        every_minute = {'update_interval': '60s'}
        return {
            'sensor': [dict(platform='wifi_signal', name='WiFi Signal', **every_minute),
                       dict(platform='uptime', name='Uptime', **every_minute)],
            'binary_sensor': [{'platform': 'status', 'name': 'Status'}],
            'text_sensor': [{'platform': 'wifi_info', 'ip_address': {'name': 'IP Address'}},
                            {'platform': 'version', 'name': 'ESPHome Version'}],
            'button': [{'platform': 'restart', 'name': 'Restart'},
                       {'platform': 'safe_mode', 'name': 'Safe Mode Boot'}],
        }

    def generate_controller_config(self, controller: Dict) -> str:
        """The complete YAML of one controller."""
        doc = self._base(controller, controller_slug(controller['name']))
        if self.br.settings.get('enable_web_server'):
            doc['web_server'] = {'port': 80, 'version': 3}
        doc['light'] = self._light_entries(controller)
        doc.update(self._diagnostics())
        header = '\n'.join((
            f"# Generated by the ESP BLE Bridge add-on (firmware {BRIDGE_FIRMWARE_VERSION}).",
            "# Regenerated when lights change. To manage this file yourself, add a line that reads",
            f'# exactly "{MANUAL_MARKER}" (without the quotes).',
            '',
        ))
        return header + yaml.dump(doc, Dumper=_YamlWriter, default_flow_style=False, sort_keys=False,
                                  allow_unicode=True)

    def controller_lights(self, controller: Dict) -> List[Tuple[Dict, str]]:
        """(light, entity name) pairs for a controller, with names made unique.

        A controller gets every light unless the user picked a subset (`light_ids`). ESPHome rejects
        two entities with the same name, and app exports often reuse names, so duplicates get their ID.
        """
        chosen = controller.get('light_ids')
        chosen = None if chosen is None else set(chosen)
        picked = [light for _, light in sorted(self.br.lights.items()) if chosen is None or light['light_id'] in chosen]
        seen: Dict[str, int] = {}
        for light in picked:
            folded = light['name'].strip().lower()
            seen[folded] = seen.get(folded, 0) + 1
        pairs = []
        for light in picked:
            name = light['name'].strip() or f"BRMesh Light {light['light_id']}"
            if seen[name.lower()] > 1 or name.lower() == ALL_LIGHTS_NAME.lower():
                name = f"{name} ({light['light_id']})"
            pairs.append((light, name))
        return pairs

    @staticmethod
    def is_manual(content: str) -> bool:
        return any(line.strip() == MANUAL_MARKER for line in content.splitlines())

    def config_path(self, controller: Dict) -> str:
        return os.path.join(self.config_dir, controller_slug(controller['name']) + '.yaml')

    def _write(self, controller: Dict, force: bool) -> Dict:
        path = self.config_path(controller)
        new = self.generate_controller_config(controller)
        old = None
        if os.path.exists(path):
            with open(path) as f:
                old = f.read()
        if old is not None and self.is_manual(old):
            return {'path': path, 'status': 'manual_override'}
        if old == new:
            return {'path': path, 'status': 'skipped'}
        if old is not None and not force:
            return {'path': path, 'status': 'update_available'}
        with open(path, 'w') as f:
            f.write(new)
        status = 'created' if old is None else 'updated'
        log.info(f"{status.capitalize()} {path}")
        return {'path': path, 'status': status}

    def generate_all_configs(self, force: bool = False) -> Dict[str, Dict]:
        """Write every controller's YAML, by controller name.

        Without `force` an existing file is left as it is and reported as `update_available`; a file
        with the manual marker is never touched.
        """
        outcome = {}
        for controller in self.br.controllers:
            try:
                outcome[controller['name']] = self._write(controller, force)
            except Exception as e:  # one broken controller must not stop the others
                log.error(f"Could not write the YAML of {controller['name']}: {e}")
                outcome[controller['name']] = {'path': self.config_path(controller), 'status': 'error', 'error': str(e)}
        return outcome

    # --- secrets ----------------------------------------------------------------------------------

    def ensure_secrets(self) -> None:
        """Create or fix every secret the generated YAML refers to."""
        have = self.br.store.read_secrets()
        add = {}
        if not self._is_valid_api_key(str(have.get('api_encryption_key', ''))):
            add['api_encryption_key'] = base64.b64encode(pysecrets.token_bytes(32)).decode('ascii')
        ota = str(have.get('ota_password', ''))
        if len(ota) < 8 or ota == 'your_ota_password':
            add['ota_password'] = ''.join(pysecrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        if self.br.mesh_key and str(have.get('mesh_key', '')).lower() != self.br.mesh_key:
            add['mesh_key'] = self.br.mesh_key
        domain = self.br.store.option('wifi_domain')
        if domain and have.get('wifi_domain') != domain:
            add['wifi_domain'] = domain
        if add:
            self.br.store.update_secrets(add)
            log.info(f"Secrets written: {', '.join(sorted(add))}")

    @staticmethod
    def _is_valid_api_key(key: str) -> bool:
        """ESPHome API encryption keys are 32 random bytes, base64 encoded."""
        try:
            raw = base64.b64decode(key, validate=True)
        except ValueError:
            return False
        return len(raw) == 32

    def sync_configs(self) -> Dict[str, Dict]:
        """At startup: secrets and YAMLs for new controllers; existing files are only checked."""
        if not self.br.settings.get('generate_esphome_configs', True):
            return {}
        self.ensure_secrets()
        outcome = self.generate_all_configs(force=False)
        stale = [name for name, r in outcome.items() if r['status'] == 'update_available']
        if stale:
            log.info(f"Newer YAML available for {', '.join(stale)}: regenerate it from the web UI")
        return outcome
