"""REST API and page server of the ESP BLE Bridge add-on, reached through Home Assistant Ingress.

All routes are relative to the Ingress prefix (ProxyFix), and every error is returned as
{"success": false, "error": "..."} with a 4xx/5xx status.
"""
import json
import logging
import os
import re

from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

from brmesh_export import ExportError, mask_key, parse_export, select_mesh
from effects import EFFECTS, PRESETS, kelvin_to_rgb
from esphome_generator import BRIDGE_FIRMWARE_VERSION, LIGHTS_PER_CONTROLLER_WARNING, controller_slug

log = logging.getLogger(__name__)

app = Flask(__name__, static_folder='/app/static', template_folder='/app/templates')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # an export is a few KB

NO_ENTITY = ("Home Assistant has no entity for this light yet. Install the firmware on a controller and add it "
             "under Settings → Devices & Services → ESPHome.")
CONTROLLER_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,30}')
TRUE_WORDS = ('1', 'true', 'yes')


def error(message, status=400, **extra):
    return jsonify({'success': False, 'error': message, **extra}), status


def ok(**fields):
    return jsonify({'success': True, **fields})


def body():
    return request.get_json(silent=True) or {}


def flag(options, name, default='0'):
    return str(options.get(name, default)).lower() in TRUE_WORDS


class WebUI:
    """The add-on's HTTP interface for one bridge. Routes live on the module-level `app`."""

    def __init__(self, bridge):
        self.br = bridge
        self._add_routes()

    # --- lookups -------------------------------------------------------------------------

    def controller(self, ident):
        """A controller by ID, name or slug."""
        wanted = str(ident)
        for c in self.br.controllers:
            if wanted in (str(c.get('id')), c.get('name'), controller_slug(c.get('name', ''))):
                return c
        return None

    def light_view(self, light, assigned=None):
        light_id = light['light_id']
        return {
            'id': light_id,
            'light_id': light_id,
            'name': light['name'],
            'kind': light.get('kind'),
            'supports_cwww': light.get('supports_cwww', False),
            'state': self.br.light_state(light_id),
            'location': light.get('location') or {'x': None, 'y': None},
            # None when there are no controllers at all, so the UI does not flag every light.
            'on_controller': None if assigned is None else light_id in assigned,
        }

    def controller_view(self, c):
        gen, builder = self.br.esphome_generator, self.br.esphome_builder
        status = self.br.controller_status(c)
        view = dict(c)
        view.update(
            online=bool(status['online']),
            ip=status['ip'] or c.get('ip'),
            in_home_assistant=status['in_home_assistant'],
            config_status='configured' if os.path.exists(gen.config_path(c)) else 'no_config',
            light_count=len(gen.controller_lights(c)),
            all_lights=c.get('light_ids') is None,
        )
        if view['light_count'] > LIGHTS_PER_CONTROLLER_WARNING:
            view['warning'] = (f"{view['light_count']} lights on one ESP32 may run it out of memory; give part of "
                               f"them to another controller.")
        job = builder.job(c['name'])
        view['job'] = None
        if job:
            view['job'] = {k: v for k, v in job.to_dict().items() if k != 'output'}
        return view

    @staticmethod
    def id_list(value):
        """None for "all lights", otherwise the sorted, de-duplicated integer IDs."""
        if value is None:
            return None
        ids = set()
        for item in value:
            try:
                ids.add(int(item))
            except (TypeError, ValueError):
                continue
        return sorted(ids)

    def set_lights_from(self, light_ids, data):
        """Apply a UI request (preset, white color temperature or RGB) to lights.

        Lights set by hand leave any effect they were running.
        """
        brightness = None if data.get('brightness') is None else int(data['brightness'])
        on = bool(data.get('state', True))
        preset = PRESETS.get(str(data.get('preset') or ''))
        if data.get('preset') and preset is None:
            raise ValueError(f"Unknown preset: {data['preset']}")
        preset = preset or {}
        kelvin = preset.get('kelvin') or (int(data['kelvin']) if data.get('kelvin') else None)
        rgb = None
        if kelvin is None:
            rgb = preset.get('rgb') or data.get('rgb')
            rgb = tuple(int(c) for c in rgb) if rgb is not None else None
        self.br.effects.release_lights(light_ids)
        return self.br.set_lights(light_ids, rgb, brightness, on, kelvin=kelvin)

    # --- Wi-Fi networks, stored as numbered entries in the ESPHome secrets file -------------------

    @staticmethod
    def saved_networks(secrets):
        networks = []
        index = 0
        while f'wifi_network_{index}_ssid' in secrets:
            networks.append({'id': index, 'ssid': str(secrets[f'wifi_network_{index}_ssid'])})
            index += 1
        default = int(secrets.get('default_wifi_network', 0) or 0)
        for network in networks:
            network['is_default'] = len(networks) == 1 or network['id'] == default
        if not networks and secrets.get('wifi_ssid'):
            # Only the active network is known (set up before networks were saved by number).
            networks.append({'id': -1, 'ssid': str(secrets['wifi_ssid']), 'is_default': True})
        return networks

    def use_network(self, ssid, password):
        self.br.store.update_secrets({'wifi_ssid': str(ssid), 'wifi_password': str(password)})

    # --- routes ----------------------------------------------------------------------------

    def _add_routes(self):
        br = self.br

        @app.get('/')
        def page():
            return render_template('index.html', version=BRIDGE_FIRMWARE_VERSION,
                                   addon_version=getattr(br, 'version', ''))

        @app.get('/api/config')
        def config():
            return jsonify(br.config)

        # Lights

        @app.get('/api/lights')
        def lights():
            assigned = None
            if br.controllers:
                assigned = {l['light_id'] for c in br.controllers for l, _ in br.esphome_generator.controller_lights(c)}
            return jsonify([self.light_view(br.lights[i], assigned) for i in sorted(br.lights)])

        @app.post('/api/lights/<int:light_id>')
        def set_light(light_id):
            if light_id not in br.lights:
                return error('Light not found', 404)
            try:
                done = self.set_lights_from([light_id], body())
            except (TypeError, ValueError) as e:
                return error(str(e))
            return ok() if done else error(NO_ENTITY, 409)

        @app.post('/api/lights/bulk')
        def set_many():
            """Set several lights at once; leaving out light_ids means every light."""
            data = body()
            ids = self.id_list(data.get('light_ids'))
            ids = sorted(br.lights) if ids is None else [i for i in ids if i in br.lights]
            if not ids:
                return error('No lights selected')
            try:
                done = self.set_lights_from(ids, data)
            except (TypeError, ValueError) as e:
                return error(str(e))
            return ok() if done else error(NO_ENTITY, 409)

        @app.put('/api/lights/<int:light_id>')
        def edit_light(light_id):
            light = br.lights.get(light_id)
            if light is None:
                return error('Light not found', 404)
            data = body()
            if str(data.get('name') or '').strip():
                light['name'] = str(data['name']).strip()
            if 'supports_cwww' in data:
                light['supports_cwww'] = bool(data['supports_cwww'])
            br.lights_changed()
            return ok(light=self.light_view(light))

        @app.post('/api/lights/<int:light_id>/identify')
        def identify(light_id):
            if light_id not in br.lights:
                return error('Light not found', 404)
            br.effects.release_lights([light_id])
            return ok() if br.identify(light_id) else error(NO_ENTITY, 409)

        @app.post('/api/lights/<int:light_id>/location')
        def place_light(light_id):
            if light_id not in br.lights:
                return error('Light not found', 404)
            data = body()
            br.lights[light_id]['location'] = {'x': data.get('x'), 'y': data.get('y')}
            br.save_config()
            return ok()

        @app.post('/api/lights/<int:light_id>/unpair')
        def remove_light(light_id):
            light = br.lights.pop(light_id, None)
            if light is None:
                return error('Light not found', 404)
            br.effects.release_lights([light_id])
            br.lights_changed()
            return ok(message=f"Removed light {light_id} ({light['name']}). Install the firmware on the controllers "
                              f"to drop its entity.")

        @app.get('/api/presets')
        def presets():
            return jsonify([{
                'id': key,
                'label': p['label'],
                'kelvin': p.get('kelvin'),
                'rgb': list(p['rgb']) if p.get('rgb') else None,
                'swatch': list(p.get('rgb') or kelvin_to_rgb(p['kelvin'])),
            } for key, p in PRESETS.items()])

        # Controllers

        @app.get('/api/controllers')
        def controllers():
            return jsonify([self.controller_view(c) for c in br.controllers])

        @app.post('/api/controllers')
        def new_controller():
            data = body()
            taken = {c['name'].lower() for c in br.controllers}
            name = str(data.get('name') or '').strip()
            if not name:
                name, n = 'esp-ble-bridge', 1
                while name in taken:
                    name, n = f'esp-ble-bridge-{n}', n + 1
            if not CONTROLLER_NAME.fullmatch(name):
                return error('Use letters, digits, spaces, - and _ in the name (at most 31 characters)')
            if name.lower() in taken:
                return error(f"There already is a controller named '{name}'")

            write_yaml = data.get('generate_esphome', True)
            if write_yaml:
                secrets = br.store.read_secrets()
                network = data.get('network_id')
                if network is not None and int(network) >= 0:
                    ssid = secrets.get(f'wifi_network_{network}_ssid')
                    if not ssid:
                        return error(f'Wi-Fi network {network} not found')
                    self.use_network(ssid, secrets.get(f'wifi_network_{network}_password'))
                elif data.get('wifi_ssid') and data.get('wifi_password'):
                    self.use_network(data['wifi_ssid'], data['wifi_password'])
                elif not secrets.get('wifi_ssid'):
                    return error('A new controller needs Wi-Fi credentials')

            entry = {
                'id': 1 + max([int(c.get('id', 0)) for c in br.controllers], default=0),
                'name': name,
                'board': data.get('board') or None,
                'ip_address': data.get('ip_address') or None,
                'light_ids': self.id_list(data.get('light_ids')),
                'location': {'x': None, 'y': None},
            }
            br.controllers.append(entry)
            path = None
            if write_yaml:
                br.esphome_generator.ensure_secrets()
                br.esphome_generator.generate_all_configs(force=False)
                path = br.esphome_generator.config_path(entry)
                entry['esphome_path'] = path
            br.save_config()
            return ok(id=entry['id'], name=name, esphome_path=path)

        @app.put('/api/controllers/<int:controller_id>')
        def edit_controller(controller_id):
            c = self.controller(controller_id)
            if c is None:
                return error('Controller not found', 404)
            data = body()
            changed = False
            for field in ('board', 'ip_address', 'mac'):
                if field in data:
                    c[field] = data[field] or None
                    changed = changed or field != 'mac'
            if 'light_ids' in data:
                c['light_ids'] = self.id_list(data['light_ids'])
                changed = True
            br.save_config()
            if changed:
                br.esphome_generator.generate_all_configs(force=True)
            return ok(controller=c)

        @app.put('/api/controllers/<ident>/location')
        def place_controller(ident):
            c = self.controller(ident)
            if c is None:
                return error('Controller not found', 404)
            data = body()
            c['location'] = {'x': data.get('x'), 'y': data.get('y')}
            br.save_config()
            return ok()

        @app.post('/api/controllers/<ident>/reset')
        def remove_controller(ident):
            """Forget a controller and delete its ESPHome YAML."""
            c = self.controller(ident)
            if c is None:
                return error('Controller not found', 404)
            br.controllers = [other for other in br.controllers if other is not c]
            br.save_config()
            path = br.esphome_generator.config_path(c)
            if os.path.exists(path):
                os.remove(path)
            return ok(message=f"Removed controller {c['name']}.")

        # Firmware (ESPHome YAML, builds and installs)

        @app.post('/api/esphome/generate')
        def write_configs():
            gen = br.esphome_generator
            gen.install_components()
            gen.ensure_secrets()
            results = gen.generate_all_configs(force=body().get('force', True))
            stats = {}
            for result in results.values():
                stats[result['status']] = stats.get(result['status'], 0) + 1
            return ok(results=results, stats=stats)

        def start(name, action, port='OTA'):
            try:
                job = br.esphome_builder.start(name, action, port)
            except (RuntimeError, FileNotFoundError) as e:
                return error(str(e), 409)
            return ok(started=True, job=job.to_dict())

        @app.post('/api/esphome/build/<name>')
        def build(name):
            return start(name, 'compile')

        @app.post('/api/esphome/flash/<name>')
        def flash(name):
            data = body()
            # Never install a firmware that is older than the YAML, secrets or components.
            fresh = br.esphome_builder.firmware_up_to_date(name) and not data.get('rebuild')
            return start(name, 'upload' if fresh else 'compile_upload', data.get('port') or 'OTA')

        @app.get('/api/esphome/build-status/<name>')
        def build_status(name):
            builder = br.esphome_builder
            path, job = builder.find_firmware_binary(name), builder.job(name)
            return jsonify({'built': bool(path), 'up_to_date': builder.firmware_up_to_date(name), 'path': path,
                            'job': job.to_dict() if job else None})

        @app.get('/api/esphome/job/<name>')
        def job_status(name):
            job = br.esphome_builder.job(name)
            return jsonify(job.to_dict()) if job else error('Nothing has been built for this controller yet', 404)

        @app.get('/api/esphome/logs/<name>')
        def job_output(name):
            job = br.esphome_builder.job(name)
            if job:
                return jsonify({'logs': job.to_dict()['output'], 'raw': True, 'job': job.to_dict()})
            return jsonify({'raw': True, 'is_placeholder': True,
                            'logs': f"No build output for {name} yet.\n\nThe ESP32's own logs are in the ESPHome "
                                    f"dashboard: open it from Settings → Apps → ESPHome and choose LOGS on this device."})

        @app.get('/api/esphome/ports')
        def serial_ports():
            return jsonify({'ports': br.esphome_builder.list_serial_ports()})

        @app.get('/api/esphome/firmware/<name>')
        def firmware_file(name):
            """The factory image (flash at 0x0), e.g. for web.esphome.io."""
            path = br.esphome_builder.find_firmware_binary(name)
            if not path:
                return error('No firmware has been built for this controller yet. Click Build first.', 404)
            return send_file(path, mimetype='application/octet-stream', as_attachment=True,
                             download_name=f"{controller_slug(name)}.factory.bin")

        @app.get('/api/esphome/manifest/<name>')
        def web_tools_manifest(name):
            """ESP Web Tools manifest, so the browser can flash an ESP32 on the user's computer."""
            c = self.controller(name)
            if c is None:
                return error('Controller not found', 404)
            board = (c.get('board') or br.settings.get('board') or 'esp32dev').lower()
            chip = 'ESP32'
            for variant in ('s3', 'c3', 'c6', 's2'):
                if f'esp32-{variant}' in board or f'esp32{variant}' in board:
                    chip = f'ESP32-{variant.upper()}'
            return jsonify({
                'name': f"ESP BLE Bridge ({c['name']})",
                'version': BRIDGE_FIRMWARE_VERSION,
                'new_install_prompt_erase': True,
                'builds': [{'chipFamily': chip,
                            'parts': [{'path': f"../firmware/{controller_slug(c['name'])}", 'offset': 0}]}],
            })

        @app.get('/api/esphome/download/<name>')
        def yaml_file(name):
            c = self.controller(name)
            if c is None:
                return error('Controller not found', 404)
            filename = f"{controller_slug(c['name'])}.yaml"
            return Response(br.esphome_generator.generate_controller_config(c), mimetype='text/yaml',
                            headers={'Content-Disposition': f'attachment; filename={filename}'})

        # BRMesh app export

        @app.post('/api/import/upload')
        def import_export():
            """Read a BRMesh export uploaded from the browser and show what it contains, or import it.

            Takes multipart `file`, or JSON {"text": ...} for pasted text or a share code. `apply=1`
            imports; otherwise only the preview is returned. `mesh` picks one of several meshes.
            """
            if 'file' in request.files:
                content = request.files['file'].read()
            else:
                content = body().get('text') or request.form.get('text')
            if not content:
                return error('No file or text received')
            try:
                parsed = parse_export(content)
            except ExportError as e:
                return error(str(e))
            options = {**request.args, **request.form}
            if options.get('mesh'):
                parsed = select_mesh(parsed, options['mesh'])

            known = set(br.lights)
            key = parsed['mesh_key']
            preview = {
                'source': parsed['source'],
                'mesh_id': parsed['mesh_id'],
                'meshes': [{'id': m['id'], 'label': m['label'], 'lights': len(m['lights']),
                            'mesh_key_masked': mask_key(m['mesh_key'])} for m in parsed['meshes']],
                'mesh_key_found': bool(key),
                'mesh_key_masked': mask_key(key),
                'mesh_key_differs': bool(key and br.mesh_key and key != br.mesh_key),
                'lights': [dict(l, exists=l['light_id'] in known) for l in parsed['lights']],
                'skipped': [{'light_id': d.get('light_id'), 'name': d.get('name'), 'reason': d['skip_reason']}
                            for d in parsed.get('skipped', [])],
                'groups': parsed['groups'],
            }
            if not flag(options, 'apply'):
                return ok(applied=False, preview=preview)
            result = br.apply_import(parsed, replace=flag(options, 'replace'),
                                     update_names=flag(options, 'update_names', '1'))
            return ok(applied=True, preview=preview, result=result, needs_install=bool(br.controllers))

        # Pairing reset lamps through a controller

        def pairing_target():
            data = {**request.args, **body()}
            ident = data.get('controller')
            if ident:
                return self.controller(ident), data
            return (br.controllers[0] if br.controllers else None), data

        @app.post('/api/pairing/start')
        def pairing_start():
            c, data = pairing_target()
            if c is None:
                return error('Add an ESP32 controller first (Controllers tab)')
            if not br.mesh_key:
                return error('Set a mesh key first: import your BRMesh export, or choose one in Settings')
            try:
                seconds = max(10, min(600, int(data.get('seconds') or 120)))
                accepted = br.start_pairing(c, seconds)
            except ValueError as e:
                return error(str(e))
            if not accepted:
                return error(f"{c['name']} did not accept the pairing request. Install the current firmware on it "
                             f"(Controllers tab) and check that it is online in Home Assistant.", 409)
            return ok(controller=c['name'], seconds=seconds, free_ids=len(br.free_light_ids()))

        @app.post('/api/pairing/stop')
        def pairing_stop():
            c, _ = pairing_target()
            if c is None:
                return error('Controller not found', 404)
            br.stop_pairing(c)
            return ok()

        @app.get('/api/pairing/status')
        def pairing_status():
            c, _ = pairing_target()
            if c is None:
                return error('Controller not found', 404)
            return ok(controller=c['name'], **br.pairing_status(c))

        @app.post('/api/pairing/add')
        def pairing_add():
            """Add a lamp whose pairing was not confirmed by a heartbeat (it may still have joined)."""
            data = body()
            try:
                light_id, type_code = int(data['light_id']), int(data.get('type_code') or 0)
            except (KeyError, TypeError, ValueError):
                return error('light_id is required')
            if not 1 <= light_id <= 255 or light_id in br.lights:
                return error(f'Light ID {light_id} is not free')
            if not br.add_paired_light(light_id, str(data.get('did') or ''), type_code):
                return error('This device is not a light the add-on can control')
            br.lights_changed()
            return ok()

        # Effects and scenes

        @app.get('/api/effects')
        def effects():
            return jsonify([{'name': name, 'params': e['params']} for name, e in EFFECTS.items()])

        @app.post('/api/effects/<name>')
        def effect_start(name):
            data = body()
            try:
                effect_id = br.effects.start_effect(data.get('light_ids', []), name, **(data.get('params') or {}))
            except ValueError as e:
                return error(str(e))
            return ok(effect_id=effect_id)

        @app.get('/api/effects/running')
        def effects_running():
            return jsonify(br.effects.running())

        @app.post('/api/effects/stop')
        def effect_stop():
            effect_id = body().get('effect_id')
            if effect_id:
                return ok(stopped=int(br.effects.stop_effect(effect_id)))
            return ok(stopped=br.effects.stop_all())

        @app.get('/api/scenes')
        def scenes():
            return jsonify(br.store.data.get('scenes', []))

        @app.post('/api/scenes')
        def scene_save():
            scene = body()
            if not scene.get('name'):
                return error('A scene needs a name')
            br.store.data['scenes'] = [s for s in br.store.data.get('scenes', []) if s.get('name') != scene['name']]
            br.store.data['scenes'].append(scene)
            br.save_config()
            return ok()

        @app.post('/api/scenes/<name>')
        def scene_apply(name):
            scene = next((s for s in br.store.data.get('scenes', []) if s.get('name') == name), None)
            if scene is None:
                return error('Scene not found', 404)
            try:
                br.effects.apply_scene(scene)
            except ValueError as e:
                return error(str(e))
            return ok()

        @app.delete('/api/scenes/<name>')
        def scene_delete(name):
            br.store.data['scenes'] = [s for s in br.store.data.get('scenes', []) if s.get('name') != name]
            br.save_config()
            return ok()

        # Settings

        # UI field -> stored setting
        setting_fields = {
            'map_enabled': 'map_enabled', 'latitude': 'latitude', 'longitude': 'longitude', 'zoom': 'zoom',
            'generate_esphome': 'generate_esphome_configs', 'include_all_lights_entity': 'include_all_lights_entity',
            'enable_web_server': 'enable_web_server',
        }
        defaults = {'map_enabled': True, 'latitude': 0, 'longitude': 0, 'zoom': 19, 'generate_esphome_configs': True,
                    'include_all_lights_entity': True, 'enable_web_server': False}
        # Settings that end up in the controllers' firmware.
        firmware_fields = ('adv_duration', 'adv_repeats', 'include_all_lights_entity', 'enable_web_server')

        @app.get('/api/settings')
        def settings():
            s = br.settings
            view = {field: s.get(stored, defaults[stored]) for field, stored in setting_fields.items()}
            view.update(mesh_key=br.mesh_key, adv_duration=s.get('adv_duration', 40), adv_repeats=s.get('adv_repeats', 3))
            return jsonify(view)

        @app.post('/api/settings')
        def settings_save():
            data = body()
            key = str(data.get('mesh_key', br.mesh_key) or '').strip().lower()
            if key and not re.fullmatch(r'[0-9a-f]{8}', key):
                return error('The mesh key is 8 hexadecimal characters')
            s = br.settings
            for field, stored in setting_fields.items():
                if data.get(field) is not None:
                    s[stored] = data[field]
            for field, low, high in (('adv_duration', 20, 500), ('adv_repeats', 1, 10)):
                if data.get(field) not in (None, ''):
                    s[field] = max(low, min(high, int(data[field])))
            key_changed = key != br.mesh_key
            br.mesh_key = key
            br.save_config()
            if key_changed or any(field in data for field in firmware_fields):
                br.esphome_generator.ensure_secrets()
                br.esphome_generator.generate_all_configs(force=True)
            return ok(message='Settings saved. Install the firmware on the controllers to apply ESP32 settings.')

        @app.post('/api/settings/reset')
        def settings_defaults():
            from state_store import DEFAULT_STATE
            br.store.data['settings'] = dict(DEFAULT_STATE['settings'])
            br.save_config()
            return ok(message='Settings are back to their defaults.')

        @app.get('/api/settings/export')
        def settings_export():
            backup = {
                'mesh_key': br.mesh_key,
                'lights': list(br.lights.values()),
                'controllers': br.controllers,
                'scenes': br.store.data.get('scenes', []),
                'settings': br.settings,
            }
            return Response(json.dumps(backup, indent=2), mimetype='application/json',
                            headers={'Content-Disposition': 'attachment; filename=esp_ble_bridge_config.json'})

        @app.post('/api/system/reset')
        def remove_everything():
            if not body().get('confirm'):
                return error('Send {"confirm": true} to remove all lights, controllers and scenes.')
            br.effects.stop_all()
            for c in list(br.controllers):
                path = br.esphome_generator.config_path(c)
                if os.path.exists(path):
                    os.remove(path)
            br.lights.clear()
            br.store.data.update(controllers=[], scenes=[], groups=[])
            br.save_config()
            return ok(message='Removed all lights, controllers and scenes.')

        # Wi-Fi networks

        @app.get('/api/wifi-networks')
        def networks():
            return jsonify({'networks': self.saved_networks(br.store.read_secrets())})

        @app.post('/api/wifi-networks')
        def network_add():
            data = body()
            ssid, password = data.get('ssid'), data.get('password')
            if not ssid or not password:
                return error('Enter the network name and password')
            secrets = br.store.read_secrets()
            index = 0
            while f'wifi_network_{index}_ssid' in secrets:
                index += 1
            values = {f'wifi_network_{index}_ssid': ssid, f'wifi_network_{index}_password': password}
            if not secrets.get('wifi_ssid'):
                values.update(wifi_ssid=ssid, wifi_password=password)
            br.store.update_secrets(values)
            return ok(id=index, ssid=ssid)

        @app.delete('/api/wifi-networks/<int:network_id>')
        def network_delete(network_id):
            secrets = br.store.read_secrets()
            count = 0
            while f'wifi_network_{count}_ssid' in secrets:
                count += 1
            if not 0 <= network_id < count:
                return error('Network not found', 404)
            kept = [(secrets[f'wifi_network_{i}_ssid'], secrets.get(f'wifi_network_{i}_password', ''))
                    for i in range(count) if i != network_id]
            # Renumber the remaining networks from 0.
            values = {}
            for i, (ssid, password) in enumerate(kept):
                values[f'wifi_network_{i}_ssid'] = str(ssid)
                values[f'wifi_network_{i}_password'] = str(password)
            stale = tuple(f'wifi_network_{i}_{part}' for i in range(count) for part in ('ssid', 'password'))
            br.store.update_secrets(values, remove=stale)
            return ok()

        @app.post('/api/wifi-networks/<int:network_id>/set-default')
        def network_default(network_id):
            secrets = br.store.read_secrets()
            ssid = secrets.get(f'wifi_network_{network_id}_ssid')
            if not ssid:
                return error('Network not found', 404)
            br.store.update_secrets({'default_wifi_network': network_id, 'wifi_ssid': str(ssid),
                                     'wifi_password': str(secrets.get(f'wifi_network_{network_id}_password', ''))})
            return ok()

        @app.errorhandler(413)
        def too_large(_):
            return error('That file is too large to be a BRMesh export', 413)

    def run(self, host='0.0.0.0', port=8099):
        from waitress import serve
        log.info(f"Web UI on {host}:{port}")
        serve(app, host=host, port=port, threads=8, channel_timeout=120)
