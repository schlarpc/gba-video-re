"""Instruction unit tests for the C54x core (libc54.so).

Cases: our own (CASES below, expected values from the SPRU172C / SPRU131G definitions) for the forms the
NMP firmware's hot loops use, and optionally the TI-manual-derived set of the Ghidra-Rare TMS320C54x
module (Apache-2.0, not included: C54X_CASES = the directory holding its test/emu_cases.py).

The cases are assembly text.  With GNU as for tic54x (C54X_AS = an `as` built from binutils with
--target=tic54x-coff) they are assembled on the fly; without it, our cases use the words recorded in
c54test_words.json (written by `c54test.py --record` with C54X_AS set), and the Ghidra-Rare cases skip.

State syntax: REG=hex (A/B 40-bit), status bit names (C, TC, OVA, OVB, OVM, SXM, FRCT, C16, CPL, INTM, BRAF, DP, ASM,
ARP, BIO, SMUL, SST), data#2:addr=word, data#4:addr=hi:lo (two words, hi at addr), RC=.

  python3 c54test.py            run all, print failures
"""
import os, re, sys, ctypes, subprocess, tempfile
import json
HERE = os.path.dirname(os.path.abspath(__file__))
AS = os.environ.get('C54X_AS', '')
WORDS = os.path.join(HERE, 'c54test_words.json')
RECORDED = json.load(open(WORDS)) if os.path.exists(WORDS) else {}
emu_cases = None
if os.environ.get('C54X_CASES'):
    sys.path.insert(0, os.path.join(os.environ['C54X_CASES'], 'test'))
    sys.path.insert(0, os.environ['C54X_CASES'])
    import emu_cases
import nmpm  # noqa: E402  (builds the libraries)

class C54(ctypes.Structure):
    _fields_ = [('acc', ctypes.c_uint64 * 2), ('t', ctypes.c_uint16), ('trn', ctypes.c_uint16),
                ('ar', ctypes.c_uint16 * 8), ('sp', ctypes.c_uint16), ('bk', ctypes.c_uint16),
                ('brc', ctypes.c_uint16), ('rsa', ctypes.c_uint16), ('rea', ctypes.c_uint16),
                ('st0', ctypes.c_uint16), ('st1', ctypes.c_uint16), ('pmst', ctypes.c_uint16),
                ('imr', ctypes.c_uint16), ('ifr', ctypes.c_uint16), ('xpc', ctypes.c_uint16), ('pc', ctypes.c_uint16),
                ('ram', ctypes.c_void_p), ('rom', ctypes.c_uint16 * 0x4000),
                ('bus', ctypes.c_void_p * 5), ('bio', ctypes.c_int), ('_rest', ctypes.c_uint8 * 8192)]

lib = ctypes.CDLL(nmpm.build()[1])
lib.c54_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
lib.c54_step.argtypes = [ctypes.c_void_p]
lib.c54_dread.argtypes = [ctypes.c_void_p, ctypes.c_uint16]; lib.c54_dread.restype = ctypes.c_uint16
lib.c54_dwrite.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]

ST0 = {'TC': 12, 'C': 11, 'OVA': 10, 'OVB': 9}
ST1 = {'BRAF': 15, 'CPL': 14, 'XF': 13, 'HM': 12, 'INTM': 11, 'OVM': 9, 'SXM': 8, 'C16': 7, 'FRCT': 6, 'CMPT': 5}
PMST = {'SMUL': 1, 'SST': 0}
ARS = {'AR%d' % i: i for i in range(8)}
BASE = 0x6000

def assemble(asm):
    if not AS:
        if asm not in RECORDED:
            raise RuntimeError('no C54X_AS and no recorded words for this case')
        return RECORDED[asm]
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, 't.s')
        open(src, 'w').write(' .mmregs\n .text\n' + ' nop\n' * BASE + asm + '\n_end_:\n nop\n')
        r = subprocess.run([AS, src, '-o', src + '.o'], capture_output=True, text=True)
        if r.returncode: raise RuntimeError(r.stderr)
        out = subprocess.run(['objdump', '-d', '-z', src + '.o'], capture_output=True, text=True).stdout
    words = []
    for l in out.split('\n'):
        m = re.match(r'^\s*([0-9a-f]+):\t((?:[0-9a-f]{4} )+)', l)
        if m: words += [int(x, 16) for x in m.group(2).split()]
    return words[BASE:]

def parse(items):
    d = {}
    for it in [x.strip() for x in items.split(',') if x.strip()]:
        k, v = it.split('=')
        d[k.strip()] = v.strip()
    return d

class CPU:
    def __init__(self):
        self.ram = (ctypes.c_uint16 * 0x40000)()
        self.c = C54()
        lib.c54_init(ctypes.byref(self.c), self.ram)
        self.c.pmst = 0x00A8   # OVLY=1, DROM=1, IPTR 1
        self.c.sp = 0x5000

    def set(self, st):
        c = self.c
        for k, v in st.items():
            ku = k.upper()
            if k.startswith('data#2:'):
                lib.c54_dwrite(ctypes.byref(c), int(k[7:], 16), int(v, 16))
            elif k.startswith('data#4:'):
                a = int(k[7:], 16); x = int(v, 16)
                lib.c54_dwrite(ctypes.byref(c), a, x >> 16); lib.c54_dwrite(ctypes.byref(c), a + 1, x & 0xFFFF)
            elif ku in ('A', 'B'): c.acc[ku == 'B'] = int(v, 16) & 0xFFFFFFFFFF
            elif ku in ST0: c.st0 = (c.st0 & ~(1 << ST0[ku])) | (int(v, 16) << ST0[ku])
            elif ku in ST1: c.st1 = (c.st1 & ~(1 << ST1[ku])) | (int(v, 16) << ST1[ku])
            elif ku in PMST: c.pmst = (c.pmst & ~(1 << PMST[ku])) | (int(v, 16) << PMST[ku])
            elif ku in ARS: c.ar[ARS[ku]] = int(v, 16)
            elif ku == 'DP': c.st0 = (c.st0 & ~0x1FF) | int(v, 16)
            elif ku == 'ARP': c.st0 = (c.st0 & 0x1FFF) | (int(v, 16) << 13)
            elif ku == 'ASM': c.st1 = (c.st1 & ~0x1F) | (int(v, 16) & 0x1F)
            elif ku == 'BIO': c.bio = int(v, 16)
            elif ku in ('T', 'TRN', 'SP', 'BK', 'BRC', 'XPC', 'IMR', 'IFR'): setattr(c, ku.lower(), int(v, 16))
            else: raise KeyError(k)

    def get(self, k):
        c = self.c; ku = k.upper()
        if k.startswith('data#2:'): return lib.c54_dread(ctypes.byref(c), int(k[7:], 16))
        if k.startswith('data#4:'):
            a = int(k[7:], 16); return lib.c54_dread(ctypes.byref(c), a) << 16 | lib.c54_dread(ctypes.byref(c), a + 1)
        if ku in ('A', 'B'): return c.acc[ku == 'B']
        if ku in ST0: return (c.st0 >> ST0[ku]) & 1
        if ku in ST1: return (c.st1 >> ST1[ku]) & 1
        if ku in ARS: return c.ar[ARS[ku]]
        if ku == 'DP': return c.st0 & 0x1FF
        if ku == 'ASM': return c.st1 & 0x1F
        if ku == 'RC': return 0
        return getattr(c, ku.lower())

def run_case(name, asm, init, expect):
    words = assemble(asm)
    cpu = CPU()
    for i, w in enumerate(words): cpu.ram[BASE + i] = w
    end = BASE + len(words) - 1
    st = parse(init)
    cpu.set(st)
    cpu.c.pc = BASE
    n = 0
    while cpu.c.pc != end and n < 10000:
        lib.c54_step(ctypes.byref(cpu.c)); n += 1
    bad = []
    for k, v in parse(expect).items():
        want = int(v, 16)
        got = cpu.get(k)
        if k.upper() == 'ASM': want &= 0x1F
        if got != want: bad.append('%s=%x (want %x)' % (k, got, want))
    return bad

# our own cases (expected values computed from the SPRU172C definitions)
CASES = [
    ("macsu_uns_x_signed", " macsu *ar2,*ar3,a", "A=0,FRCT=0,AR2=100,AR3=101,data#2:100=ffff,data#2:101=ffff", "A=ffffff0001,T=ffff"),
    ("macsu_pos", " macsu *ar2+,*ar3+,b", "B=10,FRCT=1,AR2=100,AR3=101,data#2:100=8000,data#2:101=4000", "B=40000010,T=8000,AR2=101,AR3=102"),
    ("mpy_xy_t", " mpy *ar2,*ar3+,a", "FRCT=1,AR2=100,AR3=101,data#2:100=c000,data#2:101=4000", "A=ffe0000000,T=c000,AR3=102"),
    ("mac_xy_srcdst", " mac *ar2,*ar3,a,b", "A=1,FRCT=0,AR2=100,AR3=101,data#2:100=3,data#2:101=5", "B=10,A=1"),
    ("mas_xy", " mas *ar4,*ar5+,a", "A=100,FRCT=0,AR4=100,AR5=101,data#2:100=3,data#2:101=5", "A=f1,AR5=102"),
    ("circ_dual_ar0", " stm #1024,bk\n stm #128,ar0\n mvdd *ar2+,*ar3+0%", "AR2=100,AR3=0b80,data#2:100=1234", "AR3=0800,data#2:0b80=1234"),
    ("circ_lk_neg", " stm #1024,bk\n mar *+ar3(-64)%", "AR3=0820", "AR3=0be0"),
    ("dld_odd", " dld *ar1,a", "AR1=101,SXM=1,data#2:100=1234,data#2:101=5678", "A=56781234"),
    ("dst_postinc2", " dst a,*ar1+", "AR1=100,A=0012345678", "AR1=102,data#2:100=1234,data#2:101=5678"),
    ("dadd_c16_0", " dadd *ar1,a", "AR1=100,A=0000ffffff,C16=0,SXM=1,data#2:100=0000,data#2:101=0001", "A=0001000000"),
    ("banz_lk_test", " banz tgt,*ar3(-1)\n ld #1,a\n b done\ntgt: ld #2,a\ndone: nop", "AR3=1,SXM=1", "A=1,AR3=1"),
    ("banz_lk_test2", " banz tgt,*ar3(-1)\n ld #1,a\n b done\ntgt: ld #2,a\ndone: nop", "AR3=5,SXM=1", "A=2,AR3=5"),
    ("sfta_left_carry32", " sfta b,5", "B=80aa001234,SXM=0,OVM=0,C=0", "B=1540024680,C=1"),
    ("rptb_count", " stm #3,brc\n rptb e-1\n add #1,a\ne: nop", "A=0,SXM=1", "A=4"),
    ("rptbd_count", " stm #2,brc\n rptbd e-1\n nop\n nop\n add #1,a\ne: nop", "A=0,SXM=1", "A=3"),
    ("rptb_rpt_last", " stm #1,brc\n rptb e-1\n nop\n rpt #2\n add #1,a\ne: nop", "A=0,SXM=1", "A=6"),
    ("norm_neg_t", " norm a", "A=0100000000,T=fff8,SXM=1", "A=01000000"),
    ("exp_neg", " exp a", "A=ff80000000", "T=0"),
    ("exp_40bit", " exp a", "A=0100000000", "T=fffe"),
    ("ld_ts", " ld *ar1,ts,b", "AR1=100,T=4,SXM=1,data#2:100=ff00", "B=fffffff000"),
    ("sub_xmem_shft", " sub *ar2+,8,a", "A=10000,SXM=1,AR2=100,data#2:100=1", "A=ff00,AR2=101"),
    ("sth_shift", " sth a,-8,*ar1", "A=ff43211234,SXM=1,AR1=100", "data#2:100=ff43"),
    ("stl_shift", " stl a,8,*ar1", "A=ff43211234,SXM=1,AR1=100", "data#2:100=3400"),
    ("sth_asm", " sth a,asm,*ar1", "A=ff43211234,ASM=1c,SXM=1,AR1=100", "data#2:100=f432"),
    ("ld_smem_shift_neg", " ld *ar1,-4,a", "AR1=100,SXM=1,data#2:100=8000", "A=fffffff800"),
    ("add_acc_asm", " add a,asm,b", "A=100,B=1,ASM=4,SXM=1", "B=1001"),
    ("subc_div", " rpt #15\n subc *ar1,a", "A=00000007,AR1=100,data#2:100=2,SXM=1", "A=00010003"),
    ("cmpr_lt", " cmpr lt,ar1", "AR0=10,AR1=5", "TC=1"),
    ("bitf_tc", " bitf *ar1,#8", "AR1=100,data#2:100=0108", "TC=1"),
    ("abdst", " abdst *ar2,*ar3", "A=fffffd0000,B=1,AR2=100,AR3=101,FRCT=0,SXM=1,data#2:100=5,data#2:101=2", "B=4,A=30000"),
    ("mvdk_rpt", " rpt #2\n mvdk *ar1+,#300h", "AR1=100,data#2:100=1,data#2:101=2,data#2:102=3", "data#2:300=1,data#2:301=2,data#2:302=3"),
    ("mvkd_rpt", " rpt #1\n mvkd #300h,*ar1+", "AR1=100,data#2:300=7,data#2:301=8", "data#2:100=7,data#2:101=8"),
    # regressions found on the real firmware
    ("sth_sst_sat_pos", " sth a,-4,*ar1", "A=7fffff0000,SXM=1,SST=1,AR1=100", "data#2:100=7fff"),   # SPRU131G Ex. 4-2
    ("sth_sst_sat_neg", " sth a,-4,*ar1", "A=8000010000,SXM=1,SST=1,AR1=100", "data#2:100=8000"),
    ("sth_sst_nosat", " sth a,-4,*ar1", "A=0012345000,SXM=1,SST=1,AR1=100", "data#2:100=0123"),
    ("dsub_sxm0_zero_ext", " dsub *ar1,a", "AR1=100,A=0081000852,C16=0,SXM=0,data#2:100=8328,data#2:101=6b73", "A=fffdd79cdf"),
    ("dadd_sxm0_zero_ext", " dadd *ar1,a", "AR1=100,A=0,C16=0,SXM=0,data#2:100=8000,data#2:101=0001", "A=0080000001"),
    ("xc_false_in_delay_slot", " bd tgt\n xc 1,aeq\n add #1,b\n add #2,b\n nop\ntgt: add #4,b", "A=1,B=0,SXM=1", "B=4"),
    ("xc_true_in_delay_slot", " bd tgt\n xc 1,aeq\n add #1,b\n add #2,b\n nop\ntgt: add #4,b", "A=0,B=0,SXM=1", "B=5"),
    ("banz_preinc_lk", " stm #3,ar5\nl: add #1,a\n banz l,*+ar5(-1)", "A=0,SXM=1", "A=3,AR5=0"),
    ("macp_rpt", " rpt #1\n macp *ar1+,2000h,a", "A=0,FRCT=0,AR1=100,data#2:100=2,data#2:101=3,data#2:2000=4,data#2:2001=5", "A=17"),
]

def main():
    if '--record' in sys.argv:
        assert AS, 'set C54X_AS'
        json.dump({a: assemble(a) for (_, a, _, _) in CASES}, open(WORDS, 'w'), indent=0, sort_keys=True)
        print('recorded %d cases -> %s' % (len(CASES), WORDS))
        return
    allc = [(n, a, i, e, 'ours') for (n, a, i, e) in CASES]
    if emu_cases and AS:
        allc = [(n, a, i, e, 'manual') for (n, a, i, e) in emu_cases.CASES] + allc
    elif emu_cases:
        print('C54X_CASES given but no C54X_AS: the Ghidra-Rare cases need the assembler; skipped')
    fails = 0
    for name, asm, init, exp, src in allc:
        try:
            bad = run_case(name, asm, init, exp)
        except Exception as ex:
            print('ERR ', name, ex); fails += 1; continue
        xfail = name.startswith('XFAIL_')
        if bool(bad) != xfail:
            fails += 1
            print('FAIL %-28s [%s] %s' % (name, src, '; '.join(bad) if bad else 'expected failure passed'))
    print('%d cases, %d failures' % (len(allc), fails))

if __name__ == '__main__':
    main()
