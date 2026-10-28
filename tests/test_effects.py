"""Tests for presets, white handling and effects (python3 -m unittest discover tests)."""
import os
import sys
import time
import types
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

import effects  # noqa: E402

try:
    import brmesh_bridge  # noqa: E402  (needs requests, like the add-on)
except ImportError:
    brmesh_bridge = None


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class FakeHA:
    available = True

    def __init__(self, states=None):
        self.by_id = dict(states or {})
        self.calls = []

    def states(self, max_age=5.0):
        return list(self.by_id.values())

    def state_of(self, entity_id):
        return self.by_id.get(entity_id)

    def call_service(self, domain, service, data, timeout=10):
        self.calls.append((service, data))
        return True


class FakeBridge:
    """Just enough of BRMeshBridge for the effects engine."""

    def __init__(self, light_ids, states=None, all_entity=None):
        self.lights = {i: {'light_id': i, 'name': f'L{i}', 'kind': 'rgbcw'} for i in light_ids}
        self.settings = {'adv_duration': 40}
        self.ha = FakeHA(states)
        self.sent = []
        self.own_off = {}
        self.all_entity = all_entity

    def light_entity(self, light_id):
        return f'light.l{light_id}'

    def all_lights_entity(self):
        return self.all_entity

    def own_off_time(self, entity):
        return self.own_off.get(entity)

    def set_lights(self, ids, rgb=None, brightness=None, state=True, kelvin=None):
        self.sent.append((list(ids), rgb, brightness, state, kelvin))
        return True


class KelvinTest(unittest.TestCase):
    def test_warm_is_orange_and_cool_is_near_white(self):
        r, g, b = effects.kelvin_to_rgb(2000)
        self.assertEqual(r, 255)
        self.assertGreater(g, b)
        self.assertLess(b, 60)
        cool = effects.kelvin_to_rgb(6500)
        self.assertTrue(all(c >= 240 for c in cool), cool)

    def test_every_preset_has_kelvin_or_rgb(self):
        for key, preset in effects.PRESETS.items():
            self.assertTrue(preset.get('kelvin') or preset.get('rgb'), key)
            self.assertTrue(preset.get('label'), key)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge([1, 2, 3])
        self.fx = effects.BRMeshEffects(self.bridge)

    def add_run(self, ids):
        run = effects.EffectRun(f'test-{len(self.fx.runs)}', 'rainbow', ids, {})
        self.fx.runs[run.id] = run
        return run

    def test_release_some_lights_keeps_effect(self):
        run = self.add_run([1, 2])
        self.assertEqual(self.fx.release_lights([1]), [])
        self.assertEqual(run.light_ids, [2])
        self.assertIn(run.id, self.fx.runs)

    def test_release_last_light_stops_effect(self):
        run = self.add_run([1, 2])
        self.assertEqual(self.fx.release_lights([1, 2]), [run.id])
        self.assertTrue(run.stop.is_set())
        self.assertEqual(self.fx.running(), [])

    def test_stop_all(self):
        a, b = self.add_run([1]), self.add_run([2])
        self.assertEqual(self.fx.stop_all(), 2)
        self.assertTrue(a.stop.is_set() and b.stop.is_set())


class OverrideTest(unittest.TestCase):
    """An effect must give a light up once it is switched off outside the effect."""

    def check(self, state, changed_offset, own_offset=None, all_entity=False):
        entity = 'light.all' if all_entity else 'light.l1'
        bridge = FakeBridge([1, 2], all_entity='light.all' if all_entity else None)
        fx = effects.BRMeshEffects(bridge)
        run = effects.EffectRun('rainbow-1', 'rainbow', [1, 2], {})
        run.started = time.time() - 30
        run.next_check = 0
        fx.runs[run.id] = run
        bridge.ha.by_id[entity] = {'entity_id': entity, 'state': state,
                                   'last_changed': iso(run.started + changed_offset)}
        if own_offset is not None:
            bridge.own_off[entity] = run.started + own_offset
        fx._check_overrides(run)
        return run

    def test_switched_off_in_home_assistant(self):
        self.assertEqual(self.check('off', 10).light_ids, [2])

    def test_off_before_effect_started_is_ignored(self):
        self.assertEqual(self.check('off', -5).light_ids, [1, 2])

    def test_own_turn_off_is_ignored(self):
        # Strobe (or a scene) switched it off itself 0.5 s earlier.
        self.assertEqual(self.check('off', 10, own_offset=9.5).light_ids, [1, 2])

    def test_old_own_turn_off_does_not_hide_user_turn_off(self):
        self.assertEqual(self.check('off', 20, own_offset=5).light_ids, [2])

    def test_on_is_not_an_override(self):
        self.assertEqual(self.check('on', 10).light_ids, [1, 2])

    def test_all_lights_off_stops_the_effect(self):
        run = self.check('off', 10, all_entity=True)
        self.assertEqual(run.light_ids, [])
        self.assertTrue(run.stop.is_set())


class EffectThreadTest(unittest.TestCase):
    def test_effect_stops_after_light_switched_off_in_home_assistant(self):
        bridge = FakeBridge([1])
        fx = effects.BRMeshEffects(bridge)
        old = effects.OVERRIDE_CHECK_INTERVAL
        effects.OVERRIDE_CHECK_INTERVAL = 0.1
        try:
            effect_id = fx.start_effect([1], 'halloween', interval=0.75)
            # Turn-offs in the first second of an effect are ignored (they predate it).
            time.sleep(1.2)
            self.assertTrue(bridge.sent, 'effect sent nothing')
            bridge.ha.by_id['light.l1'] = {'entity_id': 'light.l1', 'state': 'off',
                                           'last_changed': iso(time.time())}
            deadline = time.time() + 5
            while fx.running() and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(fx.running(), [], f'{effect_id} kept running')
            sent = len(bridge.sent)
            time.sleep(1.0)
            self.assertEqual(len(bridge.sent), sent, 'effect kept sending after it stopped')
        finally:
            effects.OVERRIDE_CHECK_INTERVAL = old

    def test_unknown_params_are_dropped(self):
        bridge = FakeBridge([1])
        fx = effects.BRMeshEffects(bridge)
        fx.start_effect([1], 'halloween', bogus=1)
        time.sleep(0.1)
        self.assertEqual(len(fx.running()), 1)
        fx.stop_all()


class SceneTest(unittest.TestCase):
    def test_scene_with_rgb_objects_and_kelvin(self):
        bridge = FakeBridge([1, 2, 3])
        fx = effects.BRMeshEffects(bridge)
        fx.apply_scene({'name': 's', 'lights': [
            {'id': 1, 'rgb': {'r': 255, 'g': 0, 'b': 0}, 'brightness': 255},
            {'id': 2, 'rgb': [255, 0, 0], 'brightness': 255},
            {'id': 3, 'kelvin': 2700, 'brightness': 128},
        ]})
        self.assertIn(([1, 2], (255, 0, 0), 255, True, None), bridge.sent)
        self.assertIn(([3], None, 128, True, 2700), bridge.sent)

    def test_effect_scene_starts_effect(self):
        bridge = FakeBridge([1, 2])
        fx = effects.BRMeshEffects(bridge)
        fx.apply_scene({'name': 'party', 'effect': 'color_loop'})
        self.assertEqual([r['effect'] for r in fx.running()], ['color_loop'])
        fx.stop_all()


@unittest.skipIf(brmesh_bridge is None, 'requests is not installed')
class BridgeWhiteTest(unittest.TestCase):
    def make(self, states, light_ids=(1, 2, 3)):
        b = brmesh_bridge.BRMeshBridge.__new__(brmesh_bridge.BRMeshBridge)
        b.ha = FakeHA(states)
        b.lights = {i: {'light_id': i, 'kind': 'rgbcw'} for i in light_ids}
        b._requested, b._own_off = {}, {}
        b.light_entity = lambda i: f'light.l{i}'
        b.all_lights_entity = lambda: None
        return b

    def test_white_uses_each_lamps_white_leds(self):
        b = self.make({
            'light.l1': {'attributes': {'supported_color_modes': ['color_temp', 'rgb'],
                                        'min_color_temp_kelvin': 2000, 'max_color_temp_kelvin': 6535}},
            'light.l2': {'attributes': {'supported_color_modes': ['rgb', 'white']}},
            'light.l3': {'attributes': {'supported_color_modes': ['rgb']}},
        })
        self.assertTrue(b.set_lights([1, 2, 3], None, 200, True, kelvin=2700))
        calls = {tuple(d['entity_id']): d for _, d in b.ha.calls}
        self.assertEqual(calls[('light.l1',)]['color_temp_kelvin'], 2700)
        self.assertEqual(calls[('light.l1',)]['brightness'], 200)
        self.assertEqual(calls[('light.l2',)]['white'], 200)
        self.assertNotIn('brightness', calls[('light.l2',)])
        self.assertEqual(calls[('light.l3',)]['rgb_color'], list(effects.kelvin_to_rgb(2700)))

    def test_kelvin_is_clamped_to_the_lamp_range(self):
        b = self.make({'light.l1': {'attributes': {'supported_color_modes': ['color_temp'],
                                                   'min_color_temp_kelvin': 2000, 'max_color_temp_kelvin': 6535}}},
                      light_ids=(1,))
        b.set_lights([1], None, None, True, kelvin=9000)
        self.assertEqual(b.ha.calls[0][1]['color_temp_kelvin'], 6535)

    def test_same_settings_share_one_call(self):
        attrs = {'attributes': {'supported_color_modes': ['color_temp', 'rgb']}}
        b = self.make({'light.l1': attrs, 'light.l2': attrs})
        b.set_lights([1, 2], None, None, True, kelvin=4000)
        self.assertEqual(len(b.ha.calls), 1)
        self.assertEqual(b.ha.calls[0][1]['entity_id'], ['light.l1', 'light.l2'])

    def test_plain_on_keeps_the_lamps_color(self):
        b = self.make({'light.l1': {}}, light_ids=(1,))
        b.set_lights([1], None, None, True)
        self.assertEqual(b.ha.calls, [('turn_on', {'entity_id': ['light.l1']})])

    def test_turn_off_is_recorded_as_own(self):
        b = self.make({'light.l1': {}}, light_ids=(1,))
        b.set_lights([1], None, None, False)
        self.assertIsNotNone(b.own_off_time('light.l1'))


if __name__ == '__main__':
    unittest.main()
