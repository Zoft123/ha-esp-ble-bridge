"""Tests for the add-on side of pairing (python3 -m unittest discover tests)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

try:
    import brmesh_bridge  # noqa: E402  (needs requests, like the add-on)
except ImportError:
    brmesh_bridge = None


class FakeHA:
    available = True

    def __init__(self, states):
        self._states = states
        self.calls = []

    def states(self, max_age=5.0):
        return self._states

    def controller_entities(self, name):
        # Like the device lookup: the device's entities, whatever their entity IDs look like.
        return [st for st in self._states if str(st['attributes'].get('friendly_name', '')).startswith(name)]

    def call_service(self, domain, service, data, timeout=10):
        self.calls.append((domain, service, data))
        return True


@unittest.skipIf(brmesh_bridge is None, 'requests is not installed')
class PairingTest(unittest.TestCase):
    def make(self, sensor_state, lights=(1, 2)):
        b = brmesh_bridge.BRMeshBridge.__new__(brmesh_bridge.BRMeshBridge)
        b.ha = FakeHA([
            {'entity_id': 'sensor.living_room_esp_pairing', 'state': sensor_state,
             'attributes': {'friendly_name': 'Living Room ESP Pairing'}},
            {'entity_id': 'sensor.living_room_esp_uptime', 'state': '10', 'attributes': {'friendly_name': 'Uptime'}},
        ])
        b.lights = {i: {'light_id': i, 'name': f'L{i}', 'kind': 'rgbcw'} for i in lights}
        b.changed = 0
        b.lights_changed = lambda: setattr(b, 'changed', b.changed + 1)
        return b

    controller = {'name': 'Living Room ESP'}

    def test_start_pairing_calls_the_controller_action_with_free_ids(self):
        b = self.make('off|')
        self.assertTrue(b.start_pairing({'name': 'living-room-esp'}, 90))
        domain, service, data = b.ha.calls[0]
        self.assertEqual((domain, service), ('esphome', 'living_room_esp_start_pairing'))
        self.assertEqual(data['seconds'], 90)
        self.assertEqual(data['light_ids'][:3], [3, 4, 5])
        self.assertNotIn(1, data['light_ids'])

    def test_confirmed_lamps_are_added_once(self):
        b = self.make('on|3,a1b2c3d4e5f6,43169,ok;4,0011223344ff,43050,wait;0,665544332211,43756,unsup;')
        status = b.pairing_status(self.controller)
        self.assertTrue(status['active'])
        self.assertEqual(status['added'], [3])
        self.assertEqual(b.lights[3]['kind'], 'rgbw')
        self.assertEqual(b.lights[3]['did'], 'a1b2c3d4e5f6')
        self.assertNotIn(4, b.lights)  # not confirmed yet
        self.assertEqual([l['state'] for l in status['lamps']], ['ok', 'wait', 'unsup'])
        self.assertEqual(b.changed, 1)
        # Polling again does not add it twice.
        again = b.pairing_status(self.controller)
        self.assertEqual(again['added'], [])
        self.assertEqual(b.changed, 1)

    def test_non_light_device_is_not_added(self):
        b = self.make('off|5,a1b2c3d4e5f6,44181,ok;')  # a socket/relay
        self.assertEqual(b.pairing_status(self.controller)['added'], [])
        self.assertNotIn(5, b.lights)

    def test_missing_sensor_means_old_firmware(self):
        b = self.make('off|')
        status = b.pairing_status({'name': 'Other ESP'})
        self.assertFalse(status['available'])

    def test_area_prefixed_entity_ids(self):
        b = self.make('off|')
        b.ha._states = [{'entity_id': 'sensor.network_closet_esp_ble_bridge_pairing', 'state': 'on|9,a1b2c3d4e5f6,43050,ok;',
                         'attributes': {'friendly_name': 'esp-ble-bridge Pairing'}}]
        status = b.pairing_status({'name': 'esp-ble-bridge'})
        self.assertTrue(status['available'])
        self.assertEqual(status['added'], [9])

    def test_garbage_entries_are_ignored(self):
        b = self.make('on|x,y;3,abc;;7,a1b2c3d4e5f6,43050,ok')
        self.assertEqual(b.pairing_status(self.controller)['added'], [7])


if __name__ == '__main__':
    unittest.main()
