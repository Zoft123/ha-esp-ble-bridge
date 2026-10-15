"""Tests for rootfs/app/brmesh_export.py using synthetic exports (python3 -m unittest discover tests)."""
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rootfs', 'app'))

from cryptography.hazmat.primitives import padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from brmesh_export import AES_IV, AES_KEY, ExportError, mask_key, parse_export, select_mesh  # noqa: E402


def encrypt(text: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return base64.encodebytes(enc.update(data) + enc.finalize()).decode()


BACKUP = {
    'Ctrlkey': '1a2b3c4d',
    'R_Ctrlkey': '',
    'BleDv': [
        {'addr': 7, 'name': 'Porch', 'type': 43050, 'groupId': 0, 'index': 1, 'did': 'aa', 'version': '6.11'},
        {'addr': 3, 'name': 'Garage', 'type': 43169, 'groupId': 2, 'index': 0, 'did': 'bb', 'version': '6.4'},
        {'addr': 0, 'name': 'invalid', 'type': 43050},
    ],
    'Group': [{'groupId': 0, 'groupName': 'All', 'index': 0}, {'groupId': 2, 'groupName': 'Outside', 'index': 1}],
    'Scene': [],
}


class ParseExportTest(unittest.TestCase):
    def test_encrypted_backup(self):
        result = parse_export(encrypt(json.dumps(BACKUP)))
        self.assertEqual(result['source'], 'brmesh_backup')
        self.assertEqual(result['mesh_key'], '1a2b3c4d')
        self.assertEqual([l['light_id'] for l in result['lights']], [3, 7])  # app order, invalid addr dropped
        garage, porch = result['lights']
        self.assertEqual((garage['kind'], garage['supports_cwww']), ('rgbw', False))
        self.assertEqual((porch['kind'], porch['supports_cwww']), ('rgbcw', True))
        groups = {g['group_id']: g['light_ids'] for g in result['groups']}
        self.assertEqual(groups, {0: [3, 7], 2: [3]})

    def test_decrypted_backup_and_bytes(self):
        result = parse_export(json.dumps(BACKUP).encode())
        self.assertEqual(len(result['lights']), 2)

    def test_share_code(self):
        share = json.dumps([{'a': 9, 'd': 'cc', 'n': 'Desk', 't': 1, 'v': '1'}]) + ';0a0b0c0d'
        result = parse_export(encrypt(share))
        self.assertEqual(result['mesh_key'], '0a0b0c0d')
        self.assertEqual(result['lights'][0]['kind'], 'rgbw')

    def test_legacy_json(self):
        result = parse_export('{"mesh_key": "30323336", "devices": [{"device_id": 10, "name": "Living"}]}')
        self.assertEqual(result['lights'][0]['light_id'], 10)

    def test_prefs_xml(self):
        xml = '<?xml version="1.0"?><map><string name="ctrlKey">deadbeef</string></map>'
        self.assertEqual(parse_export(xml)['mesh_key'], 'deadbeef')

    def test_errors(self):
        for bad in ('', 'not an export', '{}', encrypt('{"foo": 1}')):
            with self.assertRaises(ExportError):
                parse_export(bad)

    def test_all_device_kinds_and_skips(self):
        backup = {'Ctrlkey': '11223344', 'BleDv': [
            {'addr': 1, 'name': 'cct', 'type': 43051}, {'addr': 2, 'name': 'dim', 'type': 43049},
            {'addr': 3, 'name': 'rgb', 'type': 43168}, {'addr': 4, 'name': 'socket', 'type': 44181},
            {'addr': 5, 'name': 'curtain', 'type': 43499}, {'addr': 300, 'name': 'far', 'type': 43050},
            {'addr': 6, 'name': 'mystery', 'type': 49999}, {'addr': 6, 'name': 'dup', 'type': 43050},
        ]}
        result = parse_export(encrypt(json.dumps(backup)))
        kinds = {l['light_id']: l['kind'] for l in result['lights']}
        self.assertEqual(kinds, {1: 'cct', 2: 'dimmable', 3: 'rgb', 6: 'rgbcw'})
        self.assertFalse(next(l for l in result['lights'] if l['light_id'] == 6)['type_known'])
        reasons = {d['name']: d['skip_reason'] for d in result['skipped']}
        self.assertIn('not a light', reasons['socket'])
        self.assertIn('not a light', reasons['curtain'])
        self.assertIn('above 255', reasons['far'])
        self.assertIn('duplicate', reasons['dup'])

    def test_many_lights(self):
        backup = {'Ctrlkey': '11223344', 'BleDv': [{'addr': i, 'name': f'L{i}', 'type': 43169} for i in range(1, 256)]}
        self.assertEqual(len(parse_export(encrypt(json.dumps(backup)))['lights']), 255)

    def test_empty_main_mesh_uses_radar_mesh(self):
        backup = {'Ctrlkey': '11223344', 'BleDv': [], 'R_Ctrlkey': 'aabbccdd',
                  'Radar_Dv': [{'addr': 2, 'name': 'Hall', 'type': 43049}]}
        result = parse_export(encrypt(json.dumps(backup)))
        self.assertEqual((result['mesh_id'], result['mesh_key']), ('Radar_Dv', 'aabbccdd'))
        main = select_mesh(result, 'BleDv')
        self.assertEqual((main['mesh_key'], main['lights']), ('11223344', []))

    def test_mask(self):
        self.assertEqual(mask_key('1a2b3c4d'), '1a••••4d')


if __name__ == '__main__':
    unittest.main()
