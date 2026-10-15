"""Tests for the ESPHome YAML generator (python3 -m unittest discover tests)."""
import os
import sys
import tempfile
import types
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

import esphome_generator as g  # noqa: E402


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor('!secret', lambda loader, node: f"<secret {loader.construct_scalar(node)}>")


def make_generator(lights, tmp):
    bridge = types.SimpleNamespace(
        settings={'include_all_lights_entity': True},
        store=types.SimpleNamespace(option=lambda key, default=None: default),
        lights={l['light_id']: l for l in lights},
        controllers=[],
    )
    gen = g.ESPHomeConfigGenerator.__new__(g.ESPHomeConfigGenerator)
    gen.bridge, gen.config_dir = bridge, tmp
    return gen


def light(i, name, kind='rgbw'):
    return {'light_id': i, 'name': name, 'kind': kind, 'supports_cwww': kind in ('rgbcw', 'cct')}


class GeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def parse(self, gen, controller):
        return yaml.load(gen.generate_controller_config(controller), Loader=_Loader)

    def test_unique_names_and_reserved_name(self):
        gen = make_generator([light(1, 'Bedroom'), light(2, 'bedroom'), light(3, 'All Lights'), light(4, 'Hall')], self.tmp)
        names = [l['name'] for l in self.parse(gen, {'name': 'c'})['light']]
        self.assertEqual(names, ['Bedroom (1)', 'bedroom (2)', 'All Lights (3)', 'Hall', 'All Lights'])
        self.assertEqual(len(set(n.lower() for n in names)), len(names))

    def test_light_types(self):
        gen = make_generator([light(1, 'a', 'rgbcw'), light(2, 'b', 'cct'), light(3, 'c', 'dimmable')], self.tmp)
        entries = {l.get('light_id'): l for l in self.parse(gen, {'name': 'c'})['light']}
        self.assertEqual(entries[1]['light_type'], 'rgbcw')
        self.assertTrue(entries[1]['supports_cwww'])
        self.assertEqual(entries[2]['light_type'], 'cct')
        self.assertNotIn('supports_cwww', entries[3])

    def test_single_light_has_no_broadcast_entity(self):
        gen = make_generator([light(1, 'Only')], self.tmp)
        platforms = [l['platform'] for l in self.parse(gen, {'name': 'c'})['light']]
        self.assertEqual(platforms, ['fastcon'])

    def test_controller_subset_and_board(self):
        gen = make_generator([light(i, f'L{i}') for i in range(1, 11)], self.tmp)
        config = self.parse(gen, {'name': 'c', 'light_ids': [2, 5, 99], 'board': 'esp32-c3-devkitm-1'})
        self.assertEqual([l.get('light_id') for l in config['light'] if l['platform'] == 'fastcon'], [2, 5])
        self.assertEqual(config['esp32']['board'], 'esp32-c3-devkitm-1')

    def test_regenerates_unless_marked_manual(self):
        gen = make_generator([light(1, 'A'), light(2, 'B')], self.tmp)
        controller = {'name': 'c'}
        gen.bridge.controllers = [controller]
        self.assertEqual(gen.generate_all_configs()['c']['status'], 'created')
        gen.bridge.lights[3] = light(3, 'C')
        # The generated header mentions the marker; that must not count as manual management.
        self.assertEqual(gen.generate_all_configs(force=True)['c']['status'], 'updated')
        path = gen.config_path(controller)
        with open(path, 'a') as f:
            f.write('\n# manual_config: true\n')
        gen.bridge.lights[4] = light(4, 'D')
        self.assertEqual(gen.generate_all_configs(force=True)['c']['status'], 'manual_override')


if __name__ == '__main__':
    unittest.main()
