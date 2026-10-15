#!/usr/bin/env python3
"""
Web UI and REST API of the ESP BLE Bridge add-on (served through Home Assistant Ingress).
"""
import json
import logging
import os
import re

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from brmesh_export import ExportError, mask_key, parse_export, select_mesh
from effects import EFFECTS
from esphome_generator import BRIDGE_FIRMWARE_VERSION, LIGHTS_PER_CONTROLLER_WARNING, controller_slug

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='/app/static', template_folder='/app/templates')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# Exports are small; refuse anything that is clearly not one.
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

PAIRING_UNSUPPORTED = (
    "Pairing new lights from the ESP32 is not supported. Reset lights do not advertise on their own: "
    "the BRMesh app first broadcasts a scan request and answers with a pairing packet built by its "
    "native library (libbroadlink_ble.so), which has not been reverse engineered. Pair the lights "
    "with the BRMesh app, then upload its export (Import from BRMesh App) - the ESP32 controls them "
    "with the mesh key and light IDs from that file."
)


def error(message, status=400, **extra):
    return jsonify({'success': False, 'error': message, **extra}), status


class WebUI:
    def __init__(self, bridge):
        self.bridge = bridge
        self.setup_routes()

    # --- helpers -------------------------------------------------------------

    def _controller(self, ident):
        for c in self.bridge.controllers:
            if str(c.get('id')) == str(ident) or c.get('name') == ident or controller_slug(c.get('name', '')) == ident:
                return c
        return None

    def _light_json(self, light, assigned=None):
        light_id = light['light_id']
        return {
            'on_controller': (light_id in assigned) if assigned is not None else None,
            'id': light_id,
            'light_id': light_id,
            'name': light['name'],
            'kind': light.get('kind'),
            'supports_cwww': light.get('supports_cwww', False),
            'state': self.bridge.light_state(light_id),
            'location': light.get('location') or {'x': None, 'y': None},
        }

    def _clean_light_ids(self, value):
        """None means every light; otherwise a sorted list of known light IDs."""
        if value is None:
            return None
        ids = set()
        for v in value:
            try:
                ids.add(int(v))
            except (TypeError, ValueError):
                pass
        return sorted(ids)

    def _wifi_networks(self, secrets):
        networks = []
        i = 0
        while f'wifi_network_{i}_ssid' in secrets:
            networks.append({'id': i, 'ssid': str(secrets[f'wifi_network_{i}_ssid'])})
            i += 1
        default = int(secrets.get('default_wifi_network', 0) or 0)
        for n in networks:
            n['is_default'] = n['id'] == default or len(networks) == 1
        if not networks and secrets.get('wifi_ssid'):
            networks.append({'id': -1, 'ssid': str(secrets['wifi_ssid']), 'is_default': True})
        return networks

    def _set_active_wifi(self, ssid, password):
        self.bridge.store.update_secrets({'wifi_ssid': str(ssid), 'wifi_password': str(password)})

    # --- routes ----------------------------------------------------------------

    def setup_routes(self):
        bridge = self.bridge

        @app.route('/')
        def index():
            return render_template('index.html', version=BRIDGE_FIRMWARE_VERSION)

        @app.route('/api/config')
        def get_config():
            return jsonify(bridge.config)

        @app.route('/api/firmware/check')
        def check_firmware_version():
            # Firmware is built locally from the bundled components; there is nothing to download.
            return jsonify({'current_version': BRIDGE_FIRMWARE_VERSION, 'latest_version': BRIDGE_FIRMWARE_VERSION,
                            'update_available': False})

        # --- lights ---------------------------------------------------------

        @app.route('/api/lights')
        def get_lights():
            assigned = set()
            for c in bridge.controllers:
                assigned.update(l['light_id'] for l, _ in bridge.esphome_generator.controller_lights(c))
            return jsonify([self._light_json(bridge.lights[k], assigned if bridge.controllers else None)
                            for k in sorted(bridge.lights)])

        @app.route('/api/lights/<int:light_id>', methods=['POST'])
        def control_light(light_id):
            if light_id not in bridge.lights:
                return error('Light not found', 404)
            data = request.get_json(silent=True) or {}
            ok = bridge.set_light_color(light_id, tuple(data.get('rgb', [255, 255, 255])),
                                        int(data.get('brightness', 255)), bool(data.get('state', True)))
            if not ok:
                return error("Home Assistant has no entity for this light yet. Flash the controller and add it "
                             "in Settings → Devices & Services → ESPHome.", 409)
            return jsonify({'success': True})

        @app.route('/api/lights/<int:light_id>', methods=['PUT'])
        def update_light(light_id):
            light = bridge.lights.get(light_id)
            if not light:
                return error('Light not found', 404)
            data = request.get_json(silent=True) or {}
            if data.get('name'):
                light['name'] = str(data['name']).strip()
            if 'supports_cwww' in data:
                light['supports_cwww'] = bool(data['supports_cwww'])
            bridge.lights_changed()
            return jsonify({'success': True, 'light': self._light_json(light)})

        @app.route('/api/lights/<int:light_id>/location', methods=['POST'])
        def set_light_location(light_id):
            if light_id not in bridge.lights:
                return error('Light not found', 404)
            data = request.get_json(silent=True) or {}
            bridge.lights[light_id]['location'] = {'x': data.get('x'), 'y': data.get('y')}
            bridge.save_config()
            return jsonify({'success': True})

        @app.route('/api/lights/<int:light_id>/unpair', methods=['POST'])
        def unpair_light(light_id):
            light = bridge.lights.pop(light_id, None)
            if not light:
                return error('Light not found', 404)
            bridge.lights_changed()
            return jsonify({'success': True, 'message': f"Light {light_id} ({light['name']}) removed. "
                                                         f"Reinstall the controllers to drop it from the firmware."})

        @app.route('/api/lights/<int:light_id>/reset', methods=['POST'])
        def factory_reset_light(light_id):
            return error("Remote factory reset is not supported. Reset a light by switching its power off and on "
                         "5 times, then pair it again with the BRMesh app.", 501)

        # --- controllers --------------------------------------------------

        @app.route('/api/controllers')
        def get_controllers():
            out = []
            for c in bridge.controllers:
                info = dict(c)
                status = bridge.controller_status(c)
                info.update(online=bool(status['online']), ip=status['ip'] or c.get('ip'),
                            in_home_assistant=status['in_home_assistant'])
                info['config_status'] = 'configured' if os.path.exists(bridge.esphome_generator.config_path(c)) else 'no_config'
                info['light_count'] = len(bridge.esphome_generator.controller_lights(c))
                info['all_lights'] = c.get('light_ids') is None
                if info['light_count'] > LIGHTS_PER_CONTROLLER_WARNING:
                    info['warning'] = (f"{info['light_count']} lights on one ESP32 may run it out of memory; "
                                       f"assign part of the lights to another controller.")
                job = bridge.esphome_builder.job(c['name'])
                info['job'] = job.to_dict() if job else None
                if info['job']:
                    info['job'].pop('output', None)
                out.append(info)
            return jsonify(out)

        @app.route('/api/controllers', methods=['POST'])
        def add_controller():
            data = request.get_json(silent=True) or {}
            existing = {c['name'].lower() for c in bridge.controllers}
            name = (data.get('name') or '').strip()
            if not name:
                name = 'esp-ble-bridge'
                n = 1
                while name in existing:
                    name = f'esp-ble-bridge-{n}'
                    n += 1
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,30}', name):
                return error('Controller names may only contain letters, digits, spaces, - and _ (max 31 characters)')
            if name.lower() in existing:
                return error(f"A controller named '{name}' already exists")

            controller = {
                'id': max([int(c.get('id', 0)) for c in bridge.controllers] + [0]) + 1,
                'name': name,
                'board': data.get('board') or None,
                'ip_address': data.get('ip_address') or None,
                'light_ids': self._clean_light_ids(data.get('light_ids')),
                'location': {'x': None, 'y': None},
            }
            if data.get('generate_esphome', True):
                network_id = data.get('network_id')
                secrets = bridge.store.read_secrets()
                if network_id is not None and int(network_id) >= 0:
                    ssid = secrets.get(f'wifi_network_{network_id}_ssid')
                    password = secrets.get(f'wifi_network_{network_id}_password')
                    if not ssid:
                        return error(f'WiFi network {network_id} not found')
                    self._set_active_wifi(ssid, password)
                elif data.get('wifi_ssid') and data.get('wifi_password'):
                    self._set_active_wifi(data['wifi_ssid'], data['wifi_password'])
                elif not secrets.get('wifi_ssid'):
                    return error('WiFi credentials are required for new controllers')

            bridge.controllers.append(controller)
            bridge.save_config()
            path = None
            if data.get('generate_esphome', True):
                bridge.esphome_generator.ensure_secrets()
                bridge.esphome_generator.generate_all_configs(force=False)
                path = bridge.esphome_generator.config_path(controller)
                controller['esphome_path'] = path
                bridge.save_config()
            return jsonify({'success': True, 'id': controller['id'], 'name': name, 'esphome_path': path})

        @app.route('/api/controllers/<int:controller_id>', methods=['PUT'])
        def update_controller(controller_id):
            controller = self._controller(controller_id)
            if not controller:
                return error('Controller not found', 404)
            data = request.get_json(silent=True) or {}
            for key in ('ip_address', 'mac', 'board'):
                if key in data:
                    controller[key] = data[key] or None
            if 'ip' in data:
                controller['ip_address'] = data['ip'] or None
            if 'light_ids' in data:
                controller['light_ids'] = self._clean_light_ids(data['light_ids'])
            bridge.save_config()
            if any(k in data for k in ('board', 'ip_address', 'ip', 'light_ids')):
                bridge.esphome_generator.generate_all_configs(force=True)
            return jsonify({'success': True, 'controller': controller})

        @app.route('/api/controllers/<controller_id>/location', methods=['PUT'])
        def update_controller_location(controller_id):
            controller = self._controller(controller_id)
            if not controller:
                return error('Controller not found', 404)
            data = request.get_json(silent=True) or {}
            controller['location'] = {'x': data.get('x'), 'y': data.get('y')}
            bridge.save_config()
            return jsonify({'success': True})

        def _remove_controller(controller, delete_yaml):
            bridge.controllers = [c for c in bridge.controllers if c is not controller]
            bridge.save_config()
            path = bridge.esphome_generator.config_path(controller)
            if delete_yaml and os.path.exists(path):
                os.remove(path)

        @app.route('/api/controllers/<int:controller_id>', methods=['DELETE'])
        def delete_controller(controller_id):
            controller = self._controller(controller_id)
            if not controller:
                return error('Controller not found', 404)
            _remove_controller(controller, delete_yaml=False)
            return jsonify({'success': True})

        @app.route('/api/controllers/<controller_name>/reset', methods=['POST'])
        def reset_controller(controller_name):
            controller = self._controller(controller_name)
            if not controller:
                return error('Controller not found', 404)
            _remove_controller(controller, delete_yaml=True)
            return jsonify({'success': True, 'message': f"Controller {controller['name']} removed."})

        @app.route('/api/controllers/<controller_name>/signal')
        def get_controller_signal(controller_name):
            # Lamps do not report signal strength to the controller.
            return jsonify({})

        # --- ESPHome ---------------------------------------------------------

        @app.route('/api/esphome/generate', methods=['POST'])
        def generate_esphome_configs():
            data = request.get_json(silent=True) or {}
            bridge.esphome_generator.install_components()
            bridge.esphome_generator.ensure_secrets()
            results = bridge.esphome_generator.generate_all_configs(force=data.get('force', True))
            stats = {}
            for r in results.values():
                stats[r['status']] = stats.get(r['status'], 0) + 1
            return jsonify({'success': True, 'results': results, 'stats': stats})

        @app.route('/api/esphome/devices')
        def get_esphome_devices():
            devices = []
            esphome_dir = bridge.esphome_generator.config_dir
            if os.path.isdir(esphome_dir):
                for filename in sorted(os.listdir(esphome_dir)):
                    if filename.endswith('.yaml') and filename != 'secrets.yaml':
                        path = os.path.join(esphome_dir, filename)
                        try:
                            with open(path) as f:
                                is_brmesh = 'fastcon' in f.read()
                        except OSError:
                            is_brmesh = False
                        devices.append({'name': filename[:-5], 'filename': filename, 'path': path, 'is_brmesh': is_brmesh})
            return jsonify({'devices': devices})

        @app.route('/api/esphome/status/<controller_name>')
        def get_esphome_status(controller_name):
            controller = self._controller(controller_name) or {'name': controller_name}
            status = bridge.controller_status(controller)
            return jsonify({
                'name': controller_name,
                'online': bool(status['online']),
                'ip': status['ip'],
                'in_home_assistant': status['in_home_assistant'],
                'esphome_version': status['esphome_version'],
                'firmware_version': None,
                'expected_version': BRIDGE_FIRMWARE_VERSION,
                'needs_update': False,
            })

        @app.route('/api/esphome/build-status/<controller_name>')
        def get_build_status(controller_name):
            path = bridge.esphome_builder.find_firmware_binary(controller_name)
            job = bridge.esphome_builder.job(controller_name)
            return jsonify({'built': bool(path), 'path': path, 'job': job.to_dict() if job else None})

        @app.route('/api/esphome/job/<controller_name>')
        def get_job(controller_name):
            job = bridge.esphome_builder.job(controller_name)
            if not job:
                return error('No build has been started for this controller', 404)
            return jsonify(job.to_dict())

        def _start_job(controller_name, action, port='OTA'):
            try:
                job = bridge.esphome_builder.start(controller_name, action, port)
            except (RuntimeError, FileNotFoundError) as e:
                return error(str(e), 409)
            return jsonify({'success': True, 'started': True, 'job': job.to_dict()})

        @app.route('/api/esphome/build/<controller_name>', methods=['POST'])
        def build_firmware(controller_name):
            return _start_job(controller_name, 'compile')

        @app.route('/api/esphome/flash/<controller_name>', methods=['POST'])
        def flash_firmware(controller_name):
            data = request.get_json(silent=True) or {}
            port = data.get('port') or 'OTA'
            action = 'upload' if bridge.esphome_builder.find_firmware_binary(controller_name) and not data.get('rebuild') else 'compile_upload'
            return _start_job(controller_name, action, port)

        @app.route('/api/esphome/install/<controller_name>', methods=['POST'])
        def install_firmware(controller_name):
            data = request.get_json(silent=True) or {}
            return _start_job(controller_name, 'compile_upload', data.get('port') or 'OTA')

        @app.route('/api/esphome/ports')
        def get_serial_ports():
            return jsonify({'ports': bridge.esphome_builder.list_serial_ports()})

        @app.route('/api/esphome/logs/<controller_name>')
        def get_esphome_logs(controller_name):
            job = bridge.esphome_builder.job(controller_name)
            if job:
                return jsonify({'logs': job.to_dict()['output'], 'raw': True, 'job': job.to_dict()})
            return jsonify({'logs': f"No build output yet for {controller_name}.\n\n"
                                    "Live device logs: open the ESPHome dashboard (Settings → Add-ons → ESPHome → "
                                    "Open Web UI) and choose LOGS on this device.",
                            'raw': True, 'is_placeholder': True})

        @app.route('/api/esphome/download/<controller_name>')
        def download_esphome_config(controller_name):
            controller = self._controller(controller_name)
            if not controller:
                return error('Controller not found', 404)
            content = bridge.esphome_generator.generate_controller_config(controller)
            return Response(content, mimetype='text/yaml',
                            headers={'Content-Disposition': f'attachment; filename={controller_slug(controller["name"])}.yaml'})

        @app.route('/api/diagnostics/esphome-config/<controller_name>')
        def get_esphome_config(controller_name):
            controller = self._controller(controller_name)
            if not controller:
                return error('Controller not found', 404)
            path = bridge.esphome_generator.config_path(controller)
            if not os.path.exists(path):
                return error(f'Config not found: {path}', 404)
            with open(path) as f:
                return jsonify({'full_config': f.read(), 'file_path': path})

        @app.route('/api/diagnostics/secrets-check')
        def check_secrets():
            secrets = bridge.store.read_secrets()
            needed = ['wifi_ssid', 'wifi_password', 'api_encryption_key', 'ota_password', 'mesh_key']
            return jsonify({'missing': [k for k in needed if not secrets.get(k)],
                            'api_key_valid': bridge.esphome_generator._is_valid_api_key(str(secrets.get('api_encryption_key', ''))),
                            'mesh_key_matches': str(secrets.get('mesh_key', '')).lower() == bridge.mesh_key})

        # --- import ----------------------------------------------------------

        @app.route('/api/import/upload', methods=['POST'])
        def import_upload():
            """Parse (and optionally apply) a BRMesh app export uploaded from the browser.

            multipart/form-data with `file`, or JSON {"text": "..."} for a pasted share code.
            Query/form `apply=1` applies it; otherwise only a preview is returned.
            """
            content = None
            if 'file' in request.files:
                content = request.files['file'].read()
            else:
                body = request.get_json(silent=True) or {}
                content = body.get('text') or request.form.get('text')
            if not content:
                return error('No file or text received')
            try:
                parsed = parse_export(content)
            except ExportError as e:
                return error(str(e))

            options = {**request.args, **request.form}
            if options.get('mesh'):
                parsed = select_mesh(parsed, options['mesh'])
            existing = set(bridge.lights)
            preview = {
                'source': parsed['source'],
                'mesh_id': parsed['mesh_id'],
                'meshes': [{'id': m['id'], 'label': m['label'], 'lights': len(m['lights']),
                            'mesh_key_masked': mask_key(m['mesh_key'])} for m in parsed['meshes']],
                'mesh_key_found': bool(parsed['mesh_key']),
                'mesh_key_masked': mask_key(parsed['mesh_key']),
                'mesh_key_differs': bool(parsed['mesh_key'] and bridge.mesh_key and parsed['mesh_key'] != bridge.mesh_key),
                'lights': [{**l, 'exists': l['light_id'] in existing} for l in parsed['lights']],
                'skipped': [{'light_id': d.get('light_id'), 'name': d.get('name'), 'reason': d['skip_reason']}
                            for d in parsed.get('skipped', [])],
                'groups': parsed['groups'],
            }
            if str(options.get('apply', '0')) not in ('1', 'true', 'yes'):
                return jsonify({'success': True, 'applied': False, 'preview': preview})

            result = bridge.apply_import(
                parsed,
                replace=str(options.get('replace', '0')) in ('1', 'true', 'yes'),
                update_names=str(options.get('update_names', '1')) in ('1', 'true', 'yes'),
            )
            return jsonify({'success': True, 'applied': True, 'preview': preview, 'result': result,
                            'needs_install': bool(bridge.controllers)})

        @app.route('/api/import/app', methods=['POST'])
        @app.route('/api/settings/import-app', methods=['POST'])
        def import_from_app_legacy():
            return error('Upload the export file from the BRMesh app with "Import from BRMesh App".', 400)

        @app.route('/api/scan', methods=['POST'])
        @app.route('/api/pairing/discover', methods=['GET'])
        def scan_for_lights():
            return error(PAIRING_UNSUPPORTED, 501)

        @app.route('/api/pairing/pair', methods=['POST'])
        def pair_device():
            return error(PAIRING_UNSUPPORTED, 501)

        @app.route('/api/pairing/ignore', methods=['POST', 'DELETE'])
        def ignore_device():
            mac = (request.get_json(silent=True) or {}).get('mac')
            if not mac:
                return error('MAC address required')
            ignored = bridge.store.data.setdefault('ignored_devices', [])
            if request.method == 'POST' and mac not in ignored:
                ignored.append(mac)
            elif request.method == 'DELETE' and mac in ignored:
                ignored.remove(mac)
            bridge.save_config()
            return jsonify({'success': True, 'ignored_devices': ignored})

        @app.route('/api/control/send', methods=['POST'])
        def send_control_command():
            """Test buttons: switch a configured light through Home Assistant."""
            data = request.get_json(silent=True) or {}
            try:
                light_id = int(data.get('address'))
            except (TypeError, ValueError):
                return error('Address is required')
            if light_id not in bridge.lights:
                return error(f'Light {light_id} is not configured')
            payload = str(data.get('payload', '')).lower()
            on = payload not in ('00', '0', 'off')
            brightness = 255
            if len(payload) >= 4 and payload.startswith('01'):
                brightness = max(1, int(payload[2:4], 16) * 255 // 100)
            ok = bridge.set_light_color(light_id, (255, 255, 255), brightness, on)
            return (jsonify({'success': True}) if ok else
                    error('No Home Assistant entity for this light yet', 409))

        # --- effects & scenes ----------------------------------------------

        @app.route('/api/effects')
        def list_effects():
            return jsonify([{'name': n, 'params': e['params']} for n, e in EFFECTS.items()])

        @app.route('/api/effects/<effect_name>', methods=['POST'])
        def start_effect(effect_name):
            data = request.get_json(silent=True) or {}
            try:
                effect_id = bridge.effects.start_effect(data.get('light_ids', []), effect_name, **(data.get('params') or {}))
            except ValueError as e:
                return error(str(e))
            return jsonify({'success': True, 'effect_id': effect_id})

        @app.route('/api/effects/stop', methods=['POST'])
        def stop_effects():
            effect_id = (request.get_json(silent=True) or {}).get('effect_id')
            if effect_id:
                bridge.effects.stop_effect(effect_id)
            else:
                bridge.effects.stop_all()
            return jsonify({'success': True})

        @app.route('/api/scenes')
        def list_scenes():
            return jsonify(bridge.store.data.get('scenes', []))

        @app.route('/api/scenes', methods=['POST'])
        def create_scene():
            scene = request.get_json(silent=True) or {}
            if not scene.get('name'):
                return error('Scene name required')
            scenes = [s for s in bridge.store.data.get('scenes', []) if s.get('name') != scene['name']]
            scenes.append(scene)
            bridge.store.data['scenes'] = scenes
            bridge.save_config()
            return jsonify({'success': True})

        @app.route('/api/scenes/<scene_name>', methods=['POST'])
        def apply_scene(scene_name):
            scene = next((s for s in bridge.store.data.get('scenes', []) if s.get('name') == scene_name), None)
            if not scene:
                return error('Scene not found', 404)
            bridge.effects.apply_scene(scene)
            return jsonify({'success': True})

        @app.route('/api/scenes/<scene_name>', methods=['DELETE'])
        def delete_scene(scene_name):
            bridge.store.data['scenes'] = [s for s in bridge.store.data.get('scenes', []) if s.get('name') != scene_name]
            bridge.save_config()
            return jsonify({'success': True})

        # --- settings --------------------------------------------------------

        @app.route('/api/settings')
        def get_settings():
            s = bridge.settings
            return jsonify({
                'mesh_key': bridge.mesh_key,
                'map_enabled': s.get('map_enabled', True),
                'latitude': s.get('latitude', 0),
                'longitude': s.get('longitude', 0),
                'zoom': s.get('zoom', 19),
                'generate_esphome': s.get('generate_esphome_configs', True),
                'enable_nspanel': s.get('enable_nspanel_ui', False),
                'nspanel_entity_id': s.get('nspanel_entity_id', ''),
                'adv_duration': s.get('adv_duration', 40),
                'adv_repeats': s.get('adv_repeats', 3),
                'include_all_lights_entity': s.get('include_all_lights_entity', True),
                'enable_web_server': s.get('enable_web_server', False),
            })

        @app.route('/api/settings', methods=['POST'])
        def update_settings():
            data = request.get_json(silent=True) or {}
            mesh_key = str(data.get('mesh_key', bridge.mesh_key) or '').strip().lower()
            if mesh_key and not re.fullmatch(r'[0-9a-f]{8}', mesh_key):
                return error('The mesh key must be 8 hexadecimal characters')
            s = bridge.settings
            for src, dest in (('map_enabled', 'map_enabled'), ('latitude', 'latitude'), ('longitude', 'longitude'),
                              ('zoom', 'zoom'), ('generate_esphome', 'generate_esphome_configs'),
                              ('enable_nspanel', 'enable_nspanel_ui'), ('nspanel_entity_id', 'nspanel_entity_id'),
                              ('include_all_lights_entity', 'include_all_lights_entity'),
                              ('enable_web_server', 'enable_web_server')):
                if src in data and data[src] is not None:
                    s[dest] = data[src]
            for key, low, high in (('adv_duration', 20, 500), ('adv_repeats', 1, 10)):
                if data.get(key) not in (None, ''):
                    s[key] = max(low, min(high, int(data[key])))
            key_changed = mesh_key != bridge.mesh_key
            bridge.mesh_key = mesh_key
            bridge.save_config()
            if key_changed or any(k in data for k in ('adv_duration', 'adv_repeats', 'include_all_lights_entity', 'enable_web_server')):
                bridge.esphome_generator.ensure_secrets()
                bridge.esphome_generator.generate_all_configs(force=True)
            return jsonify({'success': True, 'message': 'Settings saved. Reinstall the controllers to apply ESP32 changes.'})

        @app.route('/api/settings/reset', methods=['POST'])
        def reset_settings():
            from state_store import DEFAULT_STATE
            bridge.store.data['settings'] = dict(DEFAULT_STATE['settings'])
            bridge.save_config()
            return jsonify({'success': True, 'message': 'Settings reset to defaults.'})

        @app.route('/api/settings/export')
        def export_settings():
            data = {
                'mesh_key': bridge.mesh_key,
                'lights': list(bridge.lights.values()),
                'controllers': bridge.controllers,
                'scenes': bridge.store.data.get('scenes', []),
                'settings': bridge.settings,
            }
            return Response(json.dumps(data, indent=2), mimetype='application/json',
                            headers={'Content-Disposition': 'attachment; filename=esp_ble_bridge_config.json'})

        @app.route('/api/system/reset', methods=['POST'])
        def system_reset():
            if not (request.get_json(silent=True) or {}).get('confirm'):
                return error('System reset requires confirmation. Set "confirm": true in request body.')
            bridge.effects.stop_all()
            for controller in list(bridge.controllers):
                path = bridge.esphome_generator.config_path(controller)
                if os.path.exists(path):
                    os.remove(path)
            bridge.lights.clear()
            bridge.store.data.update(controllers=[], scenes=[], groups=[])
            bridge.save_config()
            return jsonify({'success': True, 'message': 'All lights, controllers and scenes have been removed.'})

        # --- WiFi networks (stored in /config/esphome/secrets.yaml) ------------

        @app.route('/api/wifi-networks')
        def get_wifi_networks():
            return jsonify({'networks': self._wifi_networks(bridge.store.read_secrets())})

        @app.route('/api/wifi-networks', methods=['POST'])
        def add_wifi_network():
            data = request.get_json(silent=True) or {}
            ssid, password = data.get('ssid'), data.get('password')
            if not ssid or not password:
                return error('SSID and password required')
            secrets = bridge.store.read_secrets()
            i = 0
            while f'wifi_network_{i}_ssid' in secrets:
                i += 1
            values = {f'wifi_network_{i}_ssid': ssid, f'wifi_network_{i}_password': password}
            if not secrets.get('wifi_ssid'):
                values.update(wifi_ssid=ssid, wifi_password=password)
            bridge.store.update_secrets(values)
            return jsonify({'success': True, 'id': i, 'ssid': ssid})

        @app.route('/api/wifi-networks/<int:network_id>', methods=['DELETE'])
        def delete_wifi_network(network_id):
            secrets = bridge.store.read_secrets()
            networks = []
            i = 0
            while f'wifi_network_{i}_ssid' in secrets:
                if i != network_id:
                    networks.append((secrets[f'wifi_network_{i}_ssid'], secrets.get(f'wifi_network_{i}_password', '')))
                i += 1
            if network_id >= i or network_id < 0:
                return error('Network not found', 404)
            remove = tuple(f'wifi_network_{k}_{f}' for k in range(i) for f in ('ssid', 'password'))
            values = {}
            for k, (ssid, password) in enumerate(networks):
                values[f'wifi_network_{k}_ssid'] = str(ssid)
                values[f'wifi_network_{k}_password'] = str(password)
            bridge.store.update_secrets(values, remove=remove)
            return jsonify({'success': True})

        @app.route('/api/wifi-networks/<int:network_id>/set-default', methods=['POST'])
        def set_default_wifi_network(network_id):
            secrets = bridge.store.read_secrets()
            ssid = secrets.get(f'wifi_network_{network_id}_ssid')
            if not ssid:
                return error('Network not found', 404)
            bridge.store.update_secrets({'default_wifi_network': network_id, 'wifi_ssid': str(ssid),
                                         'wifi_password': str(secrets.get(f'wifi_network_{network_id}_password', ''))})
            return jsonify({'success': True})

        # --- misc ----------------------------------------------------------------

        @app.route('/api/map/satellite')
        def get_satellite_map():
            s = bridge.settings
            return jsonify({'center': {'lat': s.get('latitude', 0), 'lon': s.get('longitude', 0)}, 'zoom': s.get('zoom', 19)})

        @app.route('/api/nspanel/refresh', methods=['POST'])
        def refresh_nspanel():
            if not bridge.nspanel_ui:
                return error('NSPanel UI not enabled')
            bridge.nspanel_ui.refresh_nspanel_display()
            return jsonify({'success': True})

        @app.errorhandler(413)
        def too_large(_):
            return error('File too large for a BRMesh export', 413)

    def run(self, host='0.0.0.0', port=8099):
        from waitress import serve
        logger.info(f"Web UI listening on {host}:{port}")
        serve(app, host=host, port=port, threads=8, channel_timeout=120)
