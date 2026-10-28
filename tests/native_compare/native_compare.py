#!/usr/bin/env python3
"""Compare the firmware's FastCon encoding with the brMesh app's own native library.

Needs the app's x86_64 libbroadlink_ble.so (not part of this repository; it is in the app's
split_config.x86_64.apk) and the `unicorn` and `pyelftools` packages:

    BRMESH_NATIVE_LIB=/path/to/libbroadlink_ble.so python3 tests/native_compare/native_compare.py
"""
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from native import NativeLib  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', '..', 'esphome_components', 'fastcon')
PROBE = os.path.join(HERE, 'fw_probe')
DEFAULT_KEY = bytes([0x5e, 0x36, 0x7b, 0xc4])


def build_probe():
    subprocess.run(['g++', '-std=gnu++17', '-O1', f'-I{SRC}', '-I' + os.path.join(HERE, '..', 'fastcon_scheduler', 'stub'),
                    os.path.join(HERE, 'fw_probe.cpp'), os.path.join(SRC, 'protocol.cpp'), os.path.join(SRC, 'utils.cpp'),
                    '-o', PROBE], check=True)


class Firmware:
    def __init__(self):
        self.p = subprocess.Popen([PROBE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def ask(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        return self.p.stdout.readline().strip()

    def rf(self, addr, data):
        return bytes.fromhex(self.ask(f'rf {addr.hex()} {data.hex() or "-"}'))

    def crc(self, addr, data):
        return int(self.ask(f'crc {addr.hex()} {data.hex() or "-"}'), 16)


class App:
    """The app's encoding, composed from the native helpers exactly as its JNI functions do."""

    def __init__(self, lib):
        self.lib = lib

    def crc(self, addr, data):
        lib = self.lib
        lib.reset_heap()
        return lib.call('_Z11check_crc16PKciS0_i', lib.alloc(addr), len(addr), lib.alloc(data), len(data)) & 0xffff

    def rf(self, addr, data):
        """Java_..._get_1rf_1payload: framing, bit reversal, CRC, whitening(0x25), output from byte 15."""
        lib = self.lib
        la, ld = len(addr), len(data)
        buf = bytearray(la + ld + 18 + 2)
        buf[15], buf[16], buf[17] = 0x71, 0x0f, 0x55
        buf[18:18 + la] = addr[::-1]
        buf[18 + la:18 + la + ld] = data
        for i in range(15, 15 + la + 3):
            buf[i] = lib.call('_Z8invert_8c', buf[i]) & 0xff
        crc = self.crc(addr, data)
        buf[18 + la + ld] = crc & 0xff
        buf[19 + la + ld] = crc >> 8
        lib.reset_heap()
        ctx = lib.alloc(size=28)
        lib.call('_Z14whitening_initiPi', 37, ctx)
        p = lib.alloc(bytes(buf))
        lib.call('_Z17whitenging_encodePciPi', p, len(buf), ctx)
        out = lib.read(p, len(buf))
        return out[15:15 + la + ld + 5]

    def body(self, n, high, seq, safe, forward, data, key):
        """package_ble_fastcon_body: header + encrypted body (key None = default key)."""
        lib = self.lib
        lib.reset_heap()
        src = lib.alloc(data, size=max(len(data), 1))
        out = lib.alloc(size=len(data) + 4 + 16)
        k = lib.alloc(key) if key is not None else 0
        lib.call('_Z24package_ble_fastcon_bodyhhhhhPhiS_S_', n, high, seq, safe, forward, src, len(data), out, k)
        return lib.read(out, len(data) + 4)


def firmware_body(n, high, seq, safe, forward, data, key):
    """FastconController::generate_command_with_key before RF framing (mirrors the C++)."""
    key = key if key is not None else DEFAULT_KEY
    body = bytearray([(high & 0x0f) | ((n & 7) << 4) | (0x80 if forward else 0), seq, safe, 0]) + bytearray(data)
    body[3] = sum(b for i, b in enumerate(body) if i != 3) & 0xff
    for i in range(4):
        body[i] ^= DEFAULT_KEY[i]
    for i in range(len(data)):
        body[4 + i] ^= key[i & 3]
    return bytes(body)


def main():
    path = os.environ.get('BRMESH_NATIVE_LIB')
    if not path or not os.path.exists(path):
        print('Set BRMESH_NATIVE_LIB to the app\'s x86_64 libbroadlink_ble.so; skipping.')
        return 0
    build_probe()
    fw, app = Firmware(), App(NativeLib(path))
    rnd = random.Random(1234)
    failures = 0
    addr = bytes([0xC1, 0xC2, 0xC3])
    for case in range(400):
        data = bytes(rnd.randrange(256) for _ in range(rnd.choice([0, 1, 5, 12, 16, 18, 22])))
        a = addr if case % 4 else bytes(rnd.randrange(256) for _ in range(rnd.choice([3, 4, 5])))
        if fw.crc(a, data) != app.crc(a, data):
            failures += 1
            print(f'crc mismatch addr={a.hex()} data={data.hex()}: fw {fw.crc(a, data):04x} app {app.crc(a, data):04x}')
        if fw.rf(a, data) != app.rf(a, data):
            failures += 1
            print(f'rf mismatch addr={a.hex()} data={data.hex()}:\n  fw  {fw.rf(a, data).hex()}\n  app {app.rf(a, data).hex()}')
        n, high, seq, safe, fwd = rnd.randrange(8), rnd.randrange(16), rnd.randrange(256), rnd.randrange(256), rnd.randrange(2)
        key = None if case % 3 == 0 else bytes(rnd.randrange(256) for _ in range(4))
        d = data or b'\x00' * 12
        if firmware_body(n, high, seq, safe, fwd, d, key) != app.body(n, high, seq, safe, fwd, d, key):
            failures += 1
            print(f'body mismatch n={n} high={high} key={key and key.hex()} data={d.hex()}:\n'
                  f'  fw  {firmware_body(n, high, seq, safe, fwd, d, key).hex()}\n  app {app.body(n, high, seq, safe, fwd, d, key).hex()}')
        if failures > 10:
            break
    print('OK: firmware matches the app library' if not failures else f'{failures} mismatches')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
