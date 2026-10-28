"""Tests for finding the built firmware (python3 -m unittest discover tests)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

import esphome_builder  # noqa: E402


class FirmwarePathTest(unittest.TestCase):
    def make(self, tmp):
        builder = esphome_builder.ESPHomeBuilder.__new__(esphome_builder.ESPHomeBuilder)
        builder.build_dir = tmp
        return builder

    def test_finds_esphome_2026_build_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, '.esphome', 'build', 'living-room', 'build')
            os.makedirs(out)
            for f in ('firmware.factory.bin', 'firmware.ota.bin'):
                open(os.path.join(out, f), 'wb').close()
            builder = self.make(tmp)
            self.assertEqual(builder.find_firmware_binary('Living Room'), os.path.join(out, 'firmware.factory.bin'))
            self.assertEqual(builder.find_firmware_binary('Living Room', factory=False), os.path.join(out, 'firmware.ota.bin'))

    def test_nothing_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self.make(tmp).find_firmware_binary('living-room'))


if __name__ == '__main__':
    unittest.main()
