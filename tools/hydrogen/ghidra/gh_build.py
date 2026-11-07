import os
# Build a Ghidra project for one Hydrogen cart: ROM[0:N] @0x08000000 (Thumb), IWRAM image @0x03000000
import sys, os, struct
rom_path, name, nrom = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
W = os.environ.get('HY_WORK', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'hydrogen'))
rom = open(rom_path, 'rb').read()
# find crt table via header code at 0xf4: add r8,pc,#imm
t0 = 0x18c
ents = struct.unpack_from('<4I', rom, 0x18c)
tab = ents[0] + t0
iw_src = iw_len = None
for i in range(0, 0x30, 12):
    s, d, n = struct.unpack_from('<3I', rom, tab + i)
    if d == 0x03000000: iw_src, iw_len = s - 0x08000000, n
print('iwram image', hex(iw_src), hex(iw_len))
open(f'{W}/gh/{name}.rom.bin', 'wb').write(rom[:nrom])
open(f'{W}/gh/{name}.iwram.bin', 'wb').write(rom[iw_src:iw_src + iw_len])
import pyghidra
sys.setrecursionlimit(100000)
pyghidra.start()
from java.io import FileInputStream
from java.math import BigInteger
from ghidra.util.task import TaskMonitor
mon = TaskMonitor.DUMMY
with pyghidra.open_program(f'{W}/gh/{name}.rom.bin', project_location=f'{W}/gproj', project_name=name,
                           language='ARM:LE:32:v4t', loader='ghidra.app.util.opinion.BinaryLoader',
                           analyze=False, nested_project_location=False) as flat:
    prog = flat.getCurrentProgram()
    af = prog.getAddressFactory().getDefaultAddressSpace()
    mem = prog.getMemory()
    blk = mem.getBlocks()[0]
    mem.moveBlock(blk, af.getAddress(0x08000000), mon)
    b2 = mem.createInitializedBlock('iwram', af.getAddress(0x03000000), FileInputStream(f'{W}/gh/{name}.iwram.bin'), iw_len, mon, False)
    b2.setExecute(True); b2.setWrite(True)
    for a, n in [(0x02000000, 0x40000), (0x0300302c + 0, 0x8000 - iw_len)]:
        pass
    b3 = mem.createUninitializedBlock('ewram', af.getAddress(0x02000000), 0x40000, False); b3.setWrite(True)
    b4 = mem.createUninitializedBlock('iwram_bss', af.getAddress(0x03000000 + iw_len), 0x8000 - iw_len, False); b4.setWrite(True)
    b5 = mem.createUninitializedBlock('io', af.getAddress(0x04000000), 0x400, False); b5.setWrite(True); b5.setVolatile(True)
    b6 = mem.createUninitializedBlock('vram', af.getAddress(0x06000000), 0x18000, False); b6.setWrite(True)
    b7 = mem.createUninitializedBlock('pal', af.getAddress(0x05000000), 0x400, False); b7.setWrite(True)
    tm = prog.getProgramContext().getRegister('TMode')
    prog.getProgramContext().setValue(tm, af.getAddress(0x08000194), af.getAddress(0x08000000 + nrom - 1), BigInteger.ONE)
    flat.disassemble(af.getAddress(0x08000000))
    flat.createFunction(af.getAddress(0x08000000), 'entry')
    flat.analyzeAll(prog)
    print('functions', prog.getFunctionManager().getFunctionCount())
