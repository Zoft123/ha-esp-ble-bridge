"""ESP BLE Bridge: the add-on's main process.

It keeps the list of BRMesh lamps and ESP32 controllers, writes the controllers' ESPHome configuration
and builds their firmware, and serves the web UI. The lamps are driven by the ESP32s (ESPHome with the
fastcon components) and appear in Home Assistant through the ESPHome integration; the web UI, scenes
and effects control them through those Home Assistant entities.
"""
import os
import threading
import time
from logging import INFO, basicConfig, getLogger
from typing import Dict, List, Optional, Tuple

from brmesh_export import classify_device, mask_key
from effects import BRMeshEffects, kelvin_to_rgb
from esphome_builder import ESPHomeBuilder
from esphome_generator import PAIRING_SENSOR_NAME, ESPHomeConfigGenerator, controller_slug
from ha_api import HomeAssistantAPI
from state_store import StateStore

basicConfig(level=INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = getLogger('esp_ble_bridge')

ADDON_VERSION = '0.62.1'


class BRMeshBridge:
    """Lights, controllers and settings of the add-on, plus everything that acts on them."""

    version = ADDON_VERSION

    def __init__(self):
        self.store = StateStore()
        self.ha = HomeAssistantAPI()
        self.lights: Dict[int, dict] = {entry['light_id']: entry for entry in self.store.data['lights']}
        self.effects = BRMeshEffects(self)
        self.esphome_generator = ESPHomeConfigGenerator(self)
        self.esphome_builder = ESPHomeBuilder(self)
        # Last state requested through the web UI, used when HA has no entity yet.
        self._requested: Dict[int, dict] = {}
        # When the add-on last switched each entity off, so effects can tell its own offs from the user's.
        self._own_off: Dict[str, float] = {}

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
        """Settings plus the mesh key, lights, controllers, scenes and groups, for the web UI."""
        return {**self.settings, 'mesh_key': self.mesh_key, 'lights': list(self.lights.values()),
                'controllers': self.controllers, 'scenes': self.store.data.get('scenes', []),
                'groups': self.store.data.get('groups', [])}

    def save_config(self):
        """Write lights (sorted by ID), controllers and settings to the state file."""
        self.store.data['lights'] = [entry for _, entry in sorted(self.lights.items())]
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
                fields = ('kind', 'type_code', 'did', 'firmware')
                self.lights[light_id] = dict(
                    {key: item.get(key) for key in fields},
                    light_id=light_id, name=item['name'], supports_cwww=item['supports_cwww'],
                    color_interlock=True, location={'x': None, 'y': None})
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
            return None  # unknown light
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
            kelvin = req.get('kelvin')
            return {'state': req.get('state', False), 'brightness': req.get('brightness', 255),
                    'rgb': req.get('rgb') or list(kelvin_to_rgb(kelvin) if kelvin else (255, 255, 255)),
                    'color_mode': 'color_temp' if kelvin else 'rgb', 'color_temp_kelvin': kelvin,
                    'entity_id': entity, 'available': False}
        attrs = state.get('attributes', {})
        return dict(state=state.get('state') == 'on', brightness=attrs.get('brightness') or 0,
                    rgb=list(attrs.get('rgb_color') or [255, 255, 255]), color_mode=attrs.get('color_mode'),
                    color_temp_kelvin=attrs.get('color_temp_kelvin'), entity_id=entity,
                    available=state.get('state') not in ('unavailable', 'unknown'))

    def own_off_time(self, entity: str) -> Optional[float]:
        return self._own_off.get(entity)

    def _white_data(self, entity: str, kelvin: int, brightness: Optional[int]) -> Dict:
        """turn_on data for a white of `kelvin`, using the white LEDs the entity has."""
        state = self.ha.state_of(entity) or {}
        attrs = state.get('attributes', {})
        modes = attrs.get('supported_color_modes') or []
        if 'color_temp' in modes:
            lo = attrs.get('min_color_temp_kelvin') or 2000
            hi = attrs.get('max_color_temp_kelvin') or 6500
            return {'color_temp_kelvin': int(max(lo, min(hi, kelvin)))}
        if 'white' in modes:
            # `white` selects the white channel and sets its brightness in one go.
            return {'white': max(1, min(255, int(brightness if brightness is not None else 255)))}
        return {'rgb_color': list(kelvin_to_rgb(kelvin))}

    def set_lights(self, light_ids: List[int], rgb: Optional[Tuple[int, int, int]] = None,
                   brightness: Optional[int] = None, state: bool = True, kelvin: Optional[int] = None) -> bool:
        """Set one or more lights through Home Assistant. Same values -> one service call.

        `kelvin` asks for white of that color temperature instead of `rgb`.
        """
        entities = []
        for light_id in light_ids:
            self._requested[light_id] = {'state': state, 'brightness': brightness or 255,
                                         'rgb': list(rgb) if rgb and not kelvin else None, 'kelvin': kelvin}
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
        colored = rgb is not None or kelvin is not None
        if len(entities) == len(self.lights) > 1 and (not colored or not state or same_kind):
            all_entity = self.all_lights_entity()
            if all_entity:
                entities = [all_entity]
        if not state:
            now = time.time()
            for entity in entities:
                self._own_off[entity] = now
            return self.ha.call_service('light', 'turn_off', {'entity_id': entities})

        # Lamps of different types take white differently, so they may need separate calls.
        calls: Dict[str, Tuple[Dict, List[str]]] = {}
        for entity in entities:
            if kelvin is not None:
                data = self._white_data(entity, kelvin, brightness)
            elif rgb is not None:
                data = {'rgb_color': [int(c) for c in rgb]}
            else:
                data = {}
            if brightness is not None and 'white' not in data:
                data['brightness'] = max(1, min(255, int(brightness)))
            calls.setdefault(repr(sorted(data.items())), (data, []))[1].append(entity)
        ok = True
        for data, group in calls.values():
            ok = self.ha.call_service('light', 'turn_on', {'entity_id': group, **data}) and ok
        return ok

    def set_light_color(self, light_id: int, rgb: tuple, brightness: int = 255, state: bool = True) -> bool:
        return self.set_lights([light_id], rgb, brightness, state)

    def identify(self, light_id: int, flashes: int = 3) -> bool:
        """Blink a light so it can be found, then put it back the way it was."""
        if not self.light_entity(light_id):
            return False
        before = self.light_state(light_id)

        def blink():
            for _ in range(flashes):
                self.set_lights([light_id], None, 255, False)
                time.sleep(0.8)
                self.set_lights([light_id], None, 255, True)
                time.sleep(0.8)
            if not before['state']:
                self.set_lights([light_id], None, None, False)
            elif before.get('color_mode') == 'color_temp' and before.get('color_temp_kelvin'):
                self.set_lights([light_id], None, before['brightness'] or 255, True, kelvin=before['color_temp_kelvin'])
            elif before.get('color_mode') == 'white':
                self.set_lights([light_id], None, before['brightness'] or 255, True, kelvin=4000)
            else:
                self.set_lights([light_id], tuple(before['rgb']), before['brightness'] or 255, True)

        threading.Thread(target=blink, name=f'identify-{light_id}', daemon=True).start()
        return True

    # --- pairing (through a controller's start_pairing action) -----------------

    def _pairing_service(self, controller: Dict, action: str) -> str:
        # ESPHome API actions are registered as esphome.<node name with _>_<action>.
        return f"{controller_slug(controller['name']).replace('-', '_')}_{action}"

    def free_light_ids(self, limit: int = 64) -> List[int]:
        """Light IDs no known lamp uses, lowest first."""
        return [i for i in range(1, 256) if i not in self.lights][:limit]

    def start_pairing(self, controller: Dict, seconds: int = 120) -> bool:
        ids = self.free_light_ids()
        if not ids:
            raise ValueError('All 255 light IDs are in use')
        return self.ha.call_service('esphome', self._pairing_service(controller, 'start_pairing'),
                                    {'light_ids': ids, 'seconds': int(seconds)})

    def stop_pairing(self, controller: Dict) -> bool:
        return self.ha.call_service('esphome', self._pairing_service(controller, 'stop_pairing'), {})

    def pairing_status(self, controller: Dict) -> Dict:
        """What the controller's pairing sensor reports; lamps it confirmed are added to the light list.

        The sensor reads "on|<light id>,<device id>,<type>,<state>;..." (state ok, wait, nofree or unsup).
        """
        self.ha.states(max_age=1.0)
        entity = next((s for s in self.ha.controller_entities(controller['name'])
                       if s.get('entity_id', '').startswith('sensor.') and
                       str(s.get('attributes', {}).get('friendly_name', '')).endswith(PAIRING_SENSOR_NAME)), None)
        if entity is None:
            return {'available': False, 'active': False, 'lamps': [], 'added': []}
        active, _, entries = str(entity.get('state', '')).partition('|')
        lamps, added = [], []
        for entry in filter(None, entries.split(';')):
            parts = entry.split(',')
            if len(parts) != 4:
                continue
            try:
                light_id, did, type_code, state = int(parts[0]), parts[1], int(parts[2]), parts[3]
            except ValueError:
                continue
            lamp = {'light_id': light_id or None, 'did': did, 'type_code': type_code, 'state': state,
                    'known': light_id in self.lights}
            if state == 'ok' and light_id and light_id not in self.lights and self.add_paired_light(light_id, did, type_code):
                added.append(light_id)
                lamp['known'] = True
            info = classify_device(1, None, type_code)  # type only
            lamp['type_label'] = info.get('type_label') or info.get('skip_reason')
            lamps.append(lamp)
        if added:
            self.lights_changed()
        return {'available': True, 'active': active == 'on', 'lamps': lamps, 'added': added}

    def add_paired_light(self, light_id: int, did: str, type_code: int) -> bool:
        """Add a lamp a controller paired. Saving and regenerating is left to the caller."""
        item = classify_device(light_id, None, type_code, {'did': did})
        if item.get('skip_reason') or light_id in self.lights:
            return False
        self.lights[light_id] = {
            'light_id': light_id,
            'name': item['name'],
            'supports_cwww': item['supports_cwww'],
            'color_interlock': True,
            'kind': item['kind'],
            'type_code': type_code,
            'did': did,
            'location': {'x': None, 'y': None},
        }
        logger.info(f"Added paired lamp {did} as light {light_id} ({item['kind']})")
        return True

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

    # --- startup ---------------------------------------------------------------

    def _use_home_location(self):
        """Center the map on Home Assistant's home location until the user picks one."""
        if self.settings.get('latitude') and self.settings.get('longitude'):
            return
        home = self.ha.config() or {}
        if home.get('latitude') and home.get('longitude'):
            self.settings.update(latitude=home['latitude'], longitude=home['longitude'])
            self.store.save()
            logger.info("Map centered on Home Assistant's home location")

    def _prepare(self):
        """Startup work that may take a while: runs next to the web server."""
        try:
            self.esphome_generator.install_components()
            self.esphome_generator.sync_configs()
        except Exception as e:
            logger.error(f"Could not prepare the ESPHome configuration: {e}", exc_info=True)
        self._use_home_location()

    def run(self):
        logger.info(f"ESP BLE Bridge {ADDON_VERSION}: {len(self.lights)} lights, {len(self.controllers)} controllers, "
                    f"mesh key {mask_key(self.mesh_key) or 'not set (import your BRMesh export in the web UI)'}")
        if not os.path.exists(self.store.path):
            self.save_config()  # first start: create the state file
        threading.Thread(target=self._prepare, name='prepare', daemon=True).start()
        from web_ui import WebUI
        WebUI(self).run(host='0.0.0.0', port=8099)


if __name__ == '__main__':
    BRMeshBridge().run()
