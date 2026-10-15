#!/usr/bin/env python3
"""
ESP BLE Bridge add-on.

Keeps the list of BRMesh lights and ESP32 controllers, generates the ESPHome configuration for the
controllers, and offers a web UI to manage them. The lights themselves are controlled by the ESP32s
(ESPHome + fastcon) and show up in Home Assistant through the ESPHome integration; the web UI drives
those entities through the Home Assistant API.
"""
import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

from brmesh_export import mask_key
from effects import BRMeshEffects
from esphome_builder import ESPHomeBuilder
from esphome_generator import ESPHomeConfigGenerator
from ha_api import HomeAssistantAPI
from state_store import StateStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

ADDON_VERSION = '0.60.1'


class BRMeshBridge:
    def __init__(self):
        self.store = StateStore()
        self.ha = HomeAssistantAPI()
        self.lights: Dict[int, dict] = {l['light_id']: l for l in self.store.data['lights']}
        self.effects = BRMeshEffects(self)
        self.esphome_generator = ESPHomeConfigGenerator(self)
        self.esphome_builder = ESPHomeBuilder(self)
        self.nspanel_ui = None
        # Last state requested through the web UI, used when HA has no entity yet.
        self._requested: Dict[int, dict] = {}

    # --- state -------------------------------------------------------------

    @property
    def settings(self) -> Dict:
        return self.store.data['settings']

    @property
    def controllers(self) -> List[dict]:
        return self.store.data['controllers']

    @controllers.setter
    def controllers(self, value: List[dict]):
        self.store.data['controllers'] = value

    @property
    def mesh_key(self) -> str:
        return self.store.data.get('mesh_key', '')

    @mesh_key.setter
    def mesh_key(self, value: str):
        self.store.data['mesh_key'] = (value or '').lower()

    @property
    def config(self) -> Dict:
        """Flat view used by the web UI."""
        data = dict(self.settings)
        data.update({
            'mesh_key': self.mesh_key,
            'controllers': self.controllers,
            'lights': list(self.lights.values()),
            'scenes': self.store.data.get('scenes', []),
            'groups': self.store.data.get('groups', []),
            'ignored_devices': self.store.data.get('ignored_devices', []),
            'map_latitude': self.settings.get('latitude', 0),
            'map_longitude': self.settings.get('longitude', 0),
            'map_zoom': self.settings.get('zoom', 19),
        })
        return data

    def save_config(self):
        self.store.data['lights'] = [self.lights[k] for k in sorted(self.lights)]
        self.store.save()

    def lights_changed(self):
        """Persist and regenerate the controller YAMLs after the light list changed."""
        self.save_config()
        if self.settings.get('generate_esphome_configs', True) and self.controllers:
            self.esphome_generator.ensure_secrets()
            self.esphome_generator.generate_all_configs(force=True)

    # --- import --------------------------------------------------------------

    def apply_import(self, parsed: Dict, replace: bool = False, update_names: bool = True,
                     update_mesh_key: bool = True) -> Dict:
        """Apply the result of brmesh_export.parse_export()."""
        added, updated = [], []
        if replace:
            self.lights.clear()
        for item in parsed.get('lights', []):
            light_id = item['light_id']
            existing = self.lights.get(light_id)
            if existing is None:
                self.lights[light_id] = {
                    'light_id': light_id,
                    'name': item['name'],
                    'supports_cwww': item['supports_cwww'],
                    'color_interlock': True,
                    'kind': item.get('kind'),
                    'type_code': item.get('type_code'),
                    'did': item.get('did'),
                    'firmware': item.get('firmware'),
                    'location': {'x': None, 'y': None},
                }
                added.append(light_id)
            else:
                changed = False
                for key in ('kind', 'type_code', 'did', 'firmware', 'supports_cwww'):
                    if item.get(key) is not None and existing.get(key) != item[key]:
                        existing[key] = item[key]
                        changed = True
                if update_names and item.get('name') and existing.get('name') != item['name']:
                    existing['name'] = item['name']
                    changed = True
                if changed:
                    updated.append(light_id)

        key_changed = False
        if update_mesh_key and parsed.get('mesh_key') and parsed['mesh_key'] != self.mesh_key:
            self.mesh_key = parsed['mesh_key']
            key_changed = True
        if parsed.get('groups'):
            self.store.data['groups'] = parsed['groups']

        self.lights_changed()
        if key_changed:
            self.esphome_generator.ensure_secrets()
        logger.info(f"Imported {len(added)} new and {len(updated)} updated lights"
                    f"{', mesh key ' + mask_key(self.mesh_key) if key_changed else ''}")
        return {'added': added, 'updated': updated, 'mesh_key_changed': key_changed,
                'total_lights': len(self.lights)}

    # --- light control (through the ESPHome entities in Home Assistant) ------

    def controller_names(self) -> List[str]:
        return [c['name'] for c in self.controllers if c.get('name')]

    def light_entity(self, light_id: int) -> Optional[str]:
        """The Home Assistant entity of a light, on the first controller that has it."""
        if light_id not in self.lights:
            return None
        for controller in self.controllers:
            for light, name in self.esphome_generator.controller_lights(controller):
                if light['light_id'] == light_id:
                    entity = self.ha.find_light_entity(name, [controller['name']])
                    if entity:
                        return entity
                    break
        return None

    def all_lights_entity(self) -> Optional[str]:
        from esphome_generator import ALL_LIGHTS_NAME
        return self.ha.find_light_entity(ALL_LIGHTS_NAME, self.controller_names())

    def light_state(self, light_id: int) -> Dict:
        entity = self.light_entity(light_id)
        state = self.ha.state_of(entity) if entity else None
        if state is None:
            req = self._requested.get(light_id, {})
            return {'state': req.get('state', False), 'brightness': req.get('brightness', 255),
                    'rgb': req.get('rgb', [255, 255, 255]), 'entity_id': entity, 'available': False}
        attrs = state.get('attributes', {})
        return {
            'state': state.get('state') == 'on',
            'brightness': attrs.get('brightness') or 0,
            'rgb': list(attrs.get('rgb_color') or [255, 255, 255]),
            'entity_id': entity,
            'available': state.get('state') not in ('unavailable', 'unknown'),
        }

    def set_lights(self, light_ids: List[int], rgb: Optional[Tuple[int, int, int]] = None,
                   brightness: Optional[int] = None, state: bool = True) -> bool:
        """Set one or more lights through Home Assistant. Same values -> one service call."""
        entities = []
        for light_id in light_ids:
            self._requested[light_id] = {'state': state, 'brightness': brightness or 255,
                                         'rgb': list(rgb) if rgb else [255, 255, 255]}
            entity = self.light_entity(light_id)
            if entity:
                entities.append(entity)
            else:
                logger.warning(f"No Home Assistant entity found for light {light_id}; is the controller flashed and added to HA?")
        if not entities:
            return False
        # Every configured light selected: use the broadcast entity, one BLE packet for all lamps.
        # The broadcast carries the RGBCW device type; the app uses it for group power on every lamp type,
        # but colors were only verified on RGBCW lamps, so mixed setups get colors per light.
        same_kind = len({l.get('kind') for l in self.lights.values()}) == 1
        if len(entities) == len(self.lights) > 1 and (rgb is None or not state or same_kind):
            all_entity = self.all_lights_entity()
            if all_entity:
                entities = [all_entity]
        if not state:
            return self.ha.call_service('light', 'turn_off', {'entity_id': entities})
        data = {'entity_id': entities}
        if rgb is not None:
            data['rgb_color'] = [int(c) for c in rgb]
        if brightness is not None:
            data['brightness'] = max(1, min(255, int(brightness)))
        return self.ha.call_service('light', 'turn_on', data)

    def set_light_color(self, light_id: int, rgb: tuple, brightness: int = 255, state: bool = True) -> bool:
        return self.set_lights([light_id], rgb, brightness, state)

    # --- controllers -----------------------------------------------------------

    def controller_status(self, controller: Dict) -> Dict:
        """Online state and IP address of a controller, from its ESPHome entities in HA."""
        status = {'online': None, 'ip': controller.get('ip_address'), 'esphome_version': None, 'in_home_assistant': False}
        for s in self.ha.controller_entities(controller['name']):
            entity_id = s['entity_id']
            status['in_home_assistant'] = True
            if entity_id.startswith('binary_sensor.') and entity_id.endswith('_status'):
                status['online'] = s.get('state') == 'on'
            elif entity_id.endswith('_ip_address') and s.get('state') not in ('unknown', 'unavailable'):
                status['ip'] = s.get('state')
            elif entity_id.endswith('_esphome_version') and s.get('state') not in ('unknown', 'unavailable'):
                status['esphome_version'] = s.get('state')
        return status

    # --- startup -----------------------------------------------------------

    def _detect_ha_location(self):
        if self.settings.get('latitude') and self.settings.get('longitude'):
            return
        config = self.ha.config()
        if config and config.get('latitude') and config.get('longitude'):
            self.settings['latitude'] = config['latitude']
            self.settings['longitude'] = config['longitude']
            self.store.save()
            logger.info("Using Home Assistant's location for the map")

    def _cleanup_old_mqtt_entities(self):
        """Older versions published MQTT lights that never controlled anything. Remove them once."""
        if self.store.data.get('mqtt_cleanup_done') or not os.getenv('MQTT_HOST'):
            return
        try:
            import paho.mqtt.client as mqtt
            ids = set(self.lights) | {int(l.get('light_id', 0)) for l in self.store.options.get('lights', []) if isinstance(l, dict)}
            client = mqtt.Client()
            if os.getenv('MQTT_USER'):
                client.username_pw_set(os.getenv('MQTT_USER'), os.getenv('MQTT_PASS', ''))
            client.connect(os.getenv('MQTT_HOST'), int(os.getenv('MQTT_PORT', '1883')), 30)
            client.loop_start()
            for light_id in ids:
                if light_id:
                    client.publish(f"homeassistant/light/brmesh_{light_id}/config", b'', retain=True).wait_for_publish(5)
            client.loop_stop()
            client.disconnect()
            self.store.data['mqtt_cleanup_done'] = True
            self.store.save()
            logger.info(f"Removed {len(ids)} stale MQTT light entities from older versions")
        except Exception as e:
            logger.debug(f"MQTT cleanup skipped: {e}")

    def _background_startup(self):
        try:
            self.store.migrate_secrets()
            self.esphome_generator.install_components()
            self.esphome_generator.sync_configs()
        except Exception as e:
            logger.error(f"ESPHome setup failed: {e}", exc_info=True)
        self._detect_ha_location()
        self._cleanup_old_mqtt_entities()
        if self.settings.get('enable_nspanel_ui'):
            try:
                from nspanel_ui import NSPanelUIGenerator
                self.nspanel_ui = NSPanelUIGenerator(self)
                self.nspanel_ui.initialize_nspanel_ui()
            except Exception as e:
                logger.warning(f"NSPanel UI not started: {e}")

    def run(self):
        logger.info("=" * 70)
        logger.info(f"ESP BLE Bridge v{ADDON_VERSION}")
        logger.info(f"  Mesh key:    {mask_key(self.mesh_key) or '(not set - import your BRMesh export in the Web UI)'}")
        logger.info(f"  Lights:      {len(self.lights)}")
        logger.info(f"  Controllers: {len(self.controllers)}")
        logger.info("=" * 70)

        # Store a copy of the state the first time we run with the new storage.
        if not os.path.exists(self.store.path):
            self.save_config()

        threading.Thread(target=self._background_startup, daemon=True).start()

        from web_ui import WebUI
        WebUI(self).run(host='0.0.0.0', port=8099)


if __name__ == '__main__':
    BRMeshBridge().run()
