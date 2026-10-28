"""Runs functions of the brMesh app's libbroadlink_ble.so (x86_64) in the Unicorn emulator.

Only self-contained helpers are called; memcpy/memset/__stack_chk_fail are stubbed.
"""
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from unicorn import UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, Uc
from unicorn.x86_const import (UC_X86_REG_FS_BASE, UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_RAX,
                               UC_X86_REG_RCX, UC_X86_REG_RDI, UC_X86_REG_RDX, UC_X86_REG_RIP,
                               UC_X86_REG_RSI, UC_X86_REG_RSP)

BASE = 0x10000000
STACK = 0x70000000
HEAP = 0x60000000
TLS = 0x50000000
STUBS = 0x40000000
ARG_REGS = [UC_X86_REG_RDI, UC_X86_REG_RSI, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9]


class NativeLib:
    def __init__(self, path):
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_64)
        with open(path, 'rb') as f:
            elf = ELFFile(f)
            top = 0
            for seg in elf.iter_segments():
                if seg['p_type'] == 'PT_LOAD':
                    top = max(top, seg['p_vaddr'] + seg['p_memsz'])
            uc.mem_map(BASE, (top + 0xfff) & ~0xfff)
            for seg in elf.iter_segments():
                if seg['p_type'] == 'PT_LOAD':
                    uc.mem_write(BASE + seg['p_vaddr'], seg.data())
            self.symbols = {}
            for sec in elf.iter_sections():
                if sec.name == '.dynsym':
                    for sym in sec.iter_symbols():
                        if sym['st_value']:
                            self.symbols[sym.name] = BASE + sym['st_value']
            # Imported functions -> stub addresses handled in Python.
            self.stubs = {}
            dynsym = elf.get_section_by_name('.dynsym')
            for sec in elf.iter_sections():
                if not isinstance(sec, RelocationSection):
                    continue
                for rel in sec.iter_relocations():
                    sym = dynsym.get_symbol(rel['r_info_sym']) if rel['r_info_sym'] else None
                    rtype = rel['r_info_type']
                    if rtype == 8:  # R_X86_64_RELATIVE
                        uc.mem_write(BASE + rel['r_offset'], struct.pack('<Q', BASE + rel['r_addend']))
                    elif sym is not None and rtype in (6, 7, 1):  # GLOB_DAT, JUMP_SLOT, 64
                        target = self.symbols.get(sym.name) if sym['st_value'] else None
                        if target is None:
                            target = STUBS + 0x10 * len(self.stubs)
                            self.stubs[target] = sym.name
                        uc.mem_write(BASE + rel['r_offset'], struct.pack('<Q', target + rel['r_addend'] * (rtype == 1)))
        uc.mem_map(STUBS, 0x10000)
        uc.mem_write(STUBS, b'\xc3' * 0x10000)  # every stub is a `ret`
        uc.mem_map(STACK - 0x100000, 0x100000)
        uc.mem_map(HEAP, 0x100000)
        uc.mem_map(TLS, 0x1000)
        uc.reg_write(UC_X86_REG_FS_BASE, TLS)
        uc.hook_add(UC_HOOK_CODE, self._stub_hook, begin=STUBS, end=STUBS + 0xfeff)

    def _stub_hook(self, uc, address, size, user):
        name = self.stubs.get(address)
        a = [uc.reg_read(r) for r in ARG_REGS]
        if name == 'memcpy' or name == 'memmove':
            uc.mem_write(a[0], bytes(uc.mem_read(a[1], a[2])))
            uc.reg_write(UC_X86_REG_RAX, a[0])
        elif name == 'memset':
            uc.mem_write(a[0], bytes([a[1] & 0xff]) * a[2])
            uc.reg_write(UC_X86_REG_RAX, a[0])
        elif name in ('__android_log_print',):
            pass
        else:
            raise RuntimeError(f'native code called unsupported import {name}')

    def alloc(self, data=b'', size=None):
        """Very small bump allocator in the emulated heap, reset per call()."""
        size = size if size is not None else len(data)
        addr = HEAP + self._heap
        self._heap += (size + 15) & ~15
        self.uc.mem_write(addr, bytes(data) + b'\0' * (size - len(data)))
        return addr

    def read(self, addr, size):
        return bytes(self.uc.mem_read(addr, size))

    def call(self, name, *args):
        """Call `name` with integer/pointer args (pass pointers from alloc())."""
        uc = self.uc
        sp = STACK - 0x1000
        ret = STUBS + 0xff00
        uc.mem_write(sp, struct.pack('<Q', ret))
        for reg, val in zip(ARG_REGS, args):
            uc.reg_write(reg, val & 0xffffffffffffffff)
        stack_args = args[6:]
        for i, val in enumerate(stack_args):
            uc.mem_write(sp + 8 + 8 * i, struct.pack('<Q', val & 0xffffffffffffffff))
        uc.reg_write(UC_X86_REG_RSP, sp)
        uc.emu_start(self.symbols[name], ret)
        return uc.reg_read(UC_X86_REG_RAX)

    def reset_heap(self):
        self._heap = 0
