#!/usr/bin/env python3
"""
Read the files the BRMesh Android app (com.brgd.brblmesh) produces, so the user can upload them
straight from the web UI.

Supported inputs:
- The app's backup/export file ("BRMESH_BACKUP..." from "Export", often renamed brmesh_export.json):
  base64 text of AES-128-CBC encrypted JSON with the keys Ctrlkey, BleDv, Group, Scene, ...
- The text inside the app's "Share" QR code: the same encryption around "<json array>;<ctrl key>".
- An already decrypted backup (the JSON object above).
- This add-on's older hand-written format: {"mesh_key": "...", "devices": [{"device_id"/"id", "name"}]}.
- The app's shared_prefs/setting.xml (only contains the mesh key).

The AES key and IV are hard-coded in the app (GeneralClass/AESOperator.java).
"""
import base64
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

AES_KEY = b"brgdbrgdbrgdbrgd"
AES_IV = b"0392039203920300"

# Light kinds the firmware supports, and what Home Assistant offers for them.
KINDS = {
    'rgbcw': {'label': 'RGB + CW/WW', 'supports_cwww': True},
    'rgbw': {'label': 'RGBW', 'supports_cwww': False},
    'rgb': {'label': 'RGB', 'supports_cwww': False},
    'cct': {'label': 'CW/WW white', 'supports_cwww': True},
    'dimmable': {'label': 'Dimmable', 'supports_cwww': False},
}

# Every light type of the brMesh app (BLEFastconHelper.isLight), by capability.
LIGHT_TYPES = {
    43050: 'rgbcw',     # LIGHT_RGBCW
    44493: 'rgbcw',     # LIGHT_CW_RGB
    43709: 'rgbcw',     # LIGHT_COMPOSE
    44235: 'rgbcw',     # LIGHT_OEM_HEXAGON
    44483: 'rgbcw',     # CAMP_LAMP
    44935: 'rgbcw',     # LIGHT_DAZZLE_NEW
    43169: 'rgbw',      # LIGHT_RGBW
    43168: 'rgb',       # LIGHT_RGB
    43051: 'cct',       # LIGHT_CCT
    43745: 'cct',       # LIGHT_W_CW
    44495: 'cct',       # LIGHT_CW_CW
    44838: 'cct',       # LIGHT_CW_CW_LS
    43754: 'cct',       # LIGHT_BURDEN_CW
    45548: 'cct', 45544: 'cct', 45546: 'cct', 45550: 'cct', 45552: 'cct',  # LIGHT_CCT_GREE_1..5
    43049: 'dimmable',  # LIGHT_PWR
    44876: 'dimmable', 45776: 'dimmable', 45559: 'dimmable',  # LIGHT_PWR_SELF_LEARN(_1/_ADC)
    44601: 'dimmable', 44060: 'dimmable',  # LIGHT_W_24G(_QLM)
    43759: 'dimmable',  # LIGHT_BURDEN_W
    45543: 'dimmable', 45545: 'dimmable', 45547: 'dimmable', 45549: 'dimmable', 45551: 'dimmable',  # PWR_GREE
    45655: 'dimmable',  # LIGHT_W_HIGH_V
}
# Other device families in a brMesh mesh (not lights), for a readable "skipped" list.
OTHER_TYPES = {
    'socket/relay': {44181, 45124, 45485, 45495, 44180, 44167, 44164, 44165, 44166, 43474, 45746, 43525, 44624,
                     43463, 43462, 44063, 43680, 45399, 45401, 45402, 45733, 45767, 44606, 44607},
    'curtain': {43499, 44113, 44112, 44243, 44111, 44724, 44726, 44725, 45153, 45610},
    'panel/remote': {43473, 43472, 43461, 43733, 43734, 43459, 43518, 44199, 45764, 44211, 43981},
    'sensor': {43505, 43516, 43791, 45659, 43808, 44207, 43951, 44571, 45652, 44603, 44895, 44880, 44274},
    'gateway/AC': {43500, 45106, 10058, 44939, 21028, 45525, 44602, 21025, 43756, 43919},
}
# The Share QR code stores the type as an index instead (SharedScanActivity.getQrCode).
SHARE_TYPE_INDEX = {0: 43050, 1: 43169, 2: 43168, 3: 43051, 4: 43049, 5: 44601}
# Light IDs above this need the app's extended (18 byte) packets, which are not implemented.
MAX_LIGHT_ID = 255


def device_family(type_code: Optional[int]) -> Optional[str]:
    for family, codes in OTHER_TYPES.items():
        if type_code in codes:
            return family
    return None


class ExportError(ValueError):
    """The uploaded data is not something we can read."""


def _decrypt(text: str) -> str:
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as e:  # pragma: no cover - esphome depends on cryptography
        raise ExportError("The 'cryptography' Python package is missing in the add-on") from e

    try:
        raw = base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except Exception as e:
        raise ExportError("File is neither JSON nor a BRMesh app export (not base64)") from e
    if not raw or len(raw) % 16:
        raise ExportError("File is not a BRMesh app export (wrong length)")

    decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        plain = unpadder.update(padded) + unpadder.finalize()
        return plain.decode('utf-8')
    except Exception as e:
        raise ExportError("Could not decrypt the file - is it an export from the BRMesh app?") from e


def _valid_key(key: Any) -> Optional[str]:
    if isinstance(key, str) and re.fullmatch(r"[0-9a-fA-F]{8}", key.strip()):
        return key.strip().lower()
    return None


def _device(addr: Any, name: Any, type_code: Any, extra: Optional[Dict] = None) -> Dict:
    """Classify one device: a light the firmware can drive, or a skipped entry with a reason."""
    try:
        type_code = int(type_code) if type_code is not None else None
    except (TypeError, ValueError):
        type_code = None
    try:
        addr = int(addr)
    except (TypeError, ValueError):
        addr = None
    name = (str(name).strip() if name else '') or (f'BRMesh Light {addr}' if addr else 'Unnamed device')

    item = {'light_id': addr, 'name': name, 'type_code': type_code}
    if extra:
        item.update(extra)
    family = device_family(type_code)
    if family:
        return {**item, 'skip_reason': f'{family}, not a light'}
    if addr is None or addr < 1:
        return {**item, 'skip_reason': 'no light ID'}
    if addr > MAX_LIGHT_ID:
        return {**item, 'skip_reason': f'light ID {addr} is above {MAX_LIGHT_ID} (extended addressing is not supported yet)'}

    kind = LIGHT_TYPES.get(type_code)
    item['type_known'] = kind is not None
    kind = kind or 'rgbcw'  # unknown light type: offer everything
    item.update(kind=kind, type_label=KINDS[kind]['label'] + ('' if item['type_known'] else ' (unknown type, assumed)'),
                supports_cwww=KINDS[kind]['supports_cwww'])
    return item


# Public name for code outside the importer (lamps paired by a controller).
classify_device = _device


def _split(devices: List[Dict]) -> Dict:
    lights, skipped, seen = [], [], set()
    for d in devices:
        if 'skip_reason' in d:
            skipped.append(d)
        elif d['light_id'] in seen:
            skipped.append({**d, 'skip_reason': f"duplicate light ID {d['light_id']}"})
        else:
            seen.add(d['light_id'])
            lights.append(d)
    return {'lights': lights, 'skipped': skipped}


def _mesh_from_backup(data: Dict, devices_key: str, key_key: str, label: str) -> Dict:
    devices = []
    for dev in data.get(devices_key) or []:
        devices.append(_device(dev.get('addr'), dev.get('name'), dev.get('type'), {
            'did': dev.get('did'),
            'group_id': dev.get('groupId', 0),
            'firmware': dev.get('version'),
            'app_index': dev.get('index'),
        }))
    devices.sort(key=lambda d: (d.get('app_index') if d.get('app_index') is not None else 1 << 30, d.get('light_id') or 0))
    split = _split(devices)
    return {'id': devices_key, 'label': label, 'mesh_key': _valid_key(data.get(key_key)), **split}


def _from_backup(data: Dict) -> Dict:
    # The app keeps two independent meshes with their own keys: the normal one and "radar" (sensor) lamps.
    meshes = [_mesh_from_backup(data, 'BleDv', 'Ctrlkey', 'Lights'),
              _mesh_from_backup(data, 'Radar_Dv', 'R_Ctrlkey', 'Radar lights')]
    meshes = [m for m in meshes if m['lights'] or m['skipped'] or (m['id'] == 'BleDv' and m['mesh_key'])]

    groups = []
    main = next((m for m in meshes if m['id'] == 'BleDv'), None)
    if main:
        for g in data.get('Group') or []:
            gid = g.get('groupId')
            if gid is None:
                continue
            members = [l['light_id'] for l in main['lights'] if gid == 0 or l.get('group_id') == gid]
            groups.append({'group_id': gid, 'name': g.get('groupName') or ('All' if gid == 0 else f'Group {gid}'),
                           'light_ids': members})
        main['groups'] = groups
    scenes = [{'name': s.get('sceneName'), 'number': s.get('sceneNumber')} for s in data.get('Scene') or []]
    return _result('brmesh_backup', meshes, scenes)


def _result(source: str, meshes: List[Dict], scenes=None) -> Dict:
    """Top level fields describe the first mesh that has lights; all meshes are listed in `meshes`."""
    for m in meshes:
        m.setdefault('groups', [])
    first = next((m for m in meshes if m['lights']), meshes[0] if meshes else
                 {'id': None, 'mesh_key': None, 'lights': [], 'skipped': [], 'groups': []})
    return {
        'source': source,
        'mesh_id': first['id'],
        'mesh_key': first['mesh_key'],
        'lights': first['lights'],
        'skipped': first['skipped'],
        'groups': first['groups'],
        'meshes': meshes,
        'app_scenes': scenes or [],
    }


def select_mesh(parsed: Dict, mesh_id: Optional[str]) -> Dict:
    """Return `parsed` with the top level fields switched to another mesh of the export."""
    for m in parsed.get('meshes', []):
        if m['id'] == mesh_id:
            return {**parsed, 'mesh_id': m['id'], 'mesh_key': m['mesh_key'], 'lights': m['lights'],
                    'skipped': m['skipped'], 'groups': m['groups']}
    return parsed


def _from_share(text: str) -> Dict:
    array_text, _, key = text.rpartition(';')
    devices = [_device(d.get('a'), d.get('n'), SHARE_TYPE_INDEX.get(d.get('t')), {'did': d.get('d'), 'firmware': d.get('v')})
               for d in json.loads(array_text)]
    return _result('brmesh_share', [{'id': 'share', 'label': 'Shared lights', 'mesh_key': _valid_key(key), **_split(devices)}])


def _from_legacy(data: Dict) -> Dict:
    devices = []
    for dev in data.get('devices') or data.get('lights') or []:
        addr = dev.get('device_id', dev.get('light_id', dev.get('id', dev.get('addr'))))
        type_code = dev.get('type_code', dev.get('type'))
        d = _device(addr, dev.get('name'), type_code if isinstance(type_code, int) else None)
        if 'skip_reason' not in d and isinstance(dev.get('supports_cwww'), bool):
            d['supports_cwww'] = dev['supports_cwww']
        devices.append(d)
    return _result('legacy_json', [{'id': 'legacy', 'label': 'Lights', 'mesh_key': _valid_key(data.get('mesh_key')), **_split(devices)}])


def _from_prefs_xml(text: str) -> Dict:
    root = ET.fromstring(text)
    values = {e.get('name'): (e.text or e.get('value')) for e in root}
    return _result('brmesh_prefs', [{'id': 'prefs', 'label': 'Mesh key', 'mesh_key': _valid_key(values.get('ctrlKey')),
                                      'lights': [], 'skipped': []}])


def parse_export(content: Any) -> Dict:
    """Parse any supported export.

    Returns {source, mesh_id, mesh_key, lights, skipped, groups, meshes, app_scenes}; the top level fields
    describe the first mesh with lights, see select_mesh() for the others.
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig', errors='replace')
    text = (content or '').strip()
    if not text:
        raise ExportError("The file is empty")

    if text.startswith('<'):
        try:
            result = _from_prefs_xml(text)
        except ET.ParseError as e:
            raise ExportError(f"Could not read the XML file: {e}") from e
    else:
        if not text.startswith(('{', '[')):
            text = _decrypt(text)
        if text.lstrip().startswith('['):
            result = _from_share(text)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise ExportError(f"Could not read the decrypted data: {e}") from e
            if not isinstance(data, dict):
                raise ExportError("Unexpected export format")
            result = _from_backup(data) if ('BleDv' in data or 'Ctrlkey' in data) else _from_legacy(data)

    if not any(m['mesh_key'] or m['lights'] for m in result['meshes']):
        raise ExportError("No mesh key or lights found in the file")
    return result


def mask_key(key: Optional[str]) -> str:
    return f"{key[:2]}••••{key[-2:]}" if key else ''
