"""Tests for the light, preset and effect routes of the web UI (needs Flask, like the add-on)."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

try:
    import web_ui  # noqa: E402
except ImportError:
    web_ui = None

import effects  # noqa: E402


class FakeBridge:
    def __init__(self):
        self.lights = {i: {'light_id': i, 'name': f'L{i}', 'kind': 'rgbcw'} for i in (1, 2, 3)}
        self.settings = {'adv_duration': 40}
        self.controllers = []
        self.calls = []
        self.identified = []
        self.ha = types.SimpleNamespace(available=False)
        self.effects = effects.BRMeshEffects(self)
        self.store = types.SimpleNamespace(data={'scenes': []})

    def set_lights(self, ids, rgb=None, brightness=None, state=True, kelvin=None):
        self.calls.append((list(ids), rgb, brightness, state, kelvin))
        return True

    def light_state(self, light_id):
        return {'state': False}

    def identify(self, light_id):
        self.identified.append(light_id)
        return True

    def save_config(self):
        pass


@unittest.skipIf(web_ui is None, 'Flask is not installed')
class WebUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = FakeBridge()
        web_ui.WebUI(cls.bridge)  # routes live on the module-level app; register once
        cls.client = web_ui.app.test_client()

    def setUp(self):
        self.bridge.calls.clear()
        self.bridge.effects.stop_all()

    def test_presets_listed_with_swatches(self):
        presets = self.client.get('/api/presets').get_json()
        ids = [p['id'] for p in presets]
        self.assertIn('warm_white', ids)
        self.assertIn('cool_white', ids)
        self.assertTrue(all(len(p['swatch']) == 3 for p in presets))

    def test_white_preset_on_one_light(self):
        r = self.client.post('/api/lights/2', json={'preset': 'warm_white', 'brightness': 100})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.bridge.calls, [([2], None, 100, True, 2700)])

    def test_color_preset_on_all_lights(self):
        r = self.client.post('/api/lights/bulk', json={'preset': 'red'})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.bridge.calls, [([1, 2, 3], (255, 0, 0), None, True, None)])

    def test_unknown_preset_is_rejected(self):
        r = self.client.post('/api/lights/1', json={'preset': 'nope'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.bridge.calls, [])

    def test_toggle_only_switches(self):
        self.client.post('/api/lights/1', json={'state': True})
        self.assertEqual(self.bridge.calls, [([1], None, None, True, None)])

    def test_setting_a_light_takes_it_out_of_its_effect(self):
        effect_id = self.client.post('/api/effects/color_loop', json={'light_ids': [1, 2]}).get_json()['effect_id']
        running = self.client.get('/api/effects/running').get_json()
        self.assertEqual([r['id'] for r in running], [effect_id])
        self.client.post('/api/lights/1', json={'preset': 'cool_white'})
        self.assertEqual(self.client.get('/api/effects/running').get_json()[0]['light_ids'], [2])
        self.client.post('/api/lights/bulk', json={'state': False})
        self.assertEqual(self.client.get('/api/effects/running').get_json(), [])

    def test_stop_all_effects(self):
        self.client.post('/api/effects/rainbow', json={'light_ids': [1]})
        self.client.post('/api/effects/fire', json={'light_ids': [2]})
        r = self.client.post('/api/effects/stop', json={}).get_json()
        self.assertEqual(r['stopped'], 2)
        self.assertEqual(self.client.get('/api/effects/running').get_json(), [])

    def test_identify(self):
        self.assertEqual(self.client.post('/api/lights/3/identify').status_code, 200)
        self.assertEqual(self.bridge.identified[-1], 3)
        self.assertEqual(self.client.post('/api/lights/99/identify').status_code, 404)

    def test_scan_endpoints_are_gone(self):
        self.assertEqual(self.client.post('/api/scan').status_code, 404)
        self.assertEqual(self.client.get('/api/pairing/discover').status_code, 404)

    def test_pairing_needs_a_controller(self):
        r = self.client.post('/api/pairing/start', json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn('controller', r.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
