import os
import sys
name = sys.argv[1]
extra = [int(x, 16) for x in sys.argv[2:]]
W = os.environ.get('HY_WORK', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'hydrogen'))
import pyghidra
sys.setrecursionlimit(100000)
pyghidra.start()
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import TaskMonitor
from java.math import BigInteger
mon = TaskMonitor.DUMMY
with pyghidra.open_program(f'{W}/gh/{name}.rom.bin', project_location=f'{W}/gproj', project_name=name,
                           analyze=False, nested_project_location=False) as flat:
    prog = flat.getCurrentProgram()
    af = prog.getAddressFactory().getDefaultAddressSpace()
    fm = prog.getFunctionManager()
    tm = prog.getProgramContext().getRegister('TMode')
    for a in extra:
        thumb = a & 1; a &= ~1
        ad = af.getAddress(a)
        if fm.getFunctionAt(ad) is None:
            if thumb: prog.getProgramContext().setValue(tm, ad, ad.add(1), BigInteger.ONE)
            flat.disassemble(ad); flat.createFunction(ad, None)
    if extra: flat.analyzeAll(prog)
    di = DecompInterface(); di.openProgram(prog)
    out = open(f'{W}/gh/{name}.c', 'w')
    for f in fm.getFunctions(True):
        r = di.decompileFunction(f, 60, mon)
        out.write(f'// ==== {f.getName()} @ {f.getEntryPoint()}\n')
        if r.decompileCompleted(): out.write(r.getDecompiledFunction().getC())
        else: out.write('// FAILED\n')
    print('n', fm.getFunctionCount())
