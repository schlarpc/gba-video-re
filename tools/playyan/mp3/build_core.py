"""Build the C VC01 core (the local emulator, $PLAYYAN_WORK/emu/lle) for the MP3 oracle, with one
addition: a set of breakpoints (vc_brk_set) so Python hooks can stub malloc and capture state.
Output goes to $MP3WORK (default $PLAYYAN_WORK/mp3work)."""
import os, subprocess
LLE = WORK + '/emu/lle'
WORK = os.environ.get('MP3WORK', os.environ.get('PLAYYAN_WORK', '') + '/mp3work')

def build():
    os.makedirs(WORK, exist_ok=True)
    lib = os.path.join(WORK, 'libvc01_mp3.so')
    srcs = [os.path.join(LLE, f) for f in ('vc01core.c', 'vc01core.h', 'vc01hw.c')]
    if os.path.exists(lib) and all(os.path.getmtime(s) < os.path.getmtime(lib) for s in srcs):
        return lib
    s = open(srcs[0]).read()
    s = s.replace("if (c->r[31] == c->break_pc && c->break_pc) { c->stop = VC_STOP_BREAK; return i; }",
                  "if (c->r[31] == c->break_pc && c->break_pc) { c->stop = VC_STOP_BREAK; return i; }\n"
                  "\t\tif (nbrk) { int k; for (k = 0; k < nbrk; k++) if (c->r[31] == brk[k] && !(skip1 && i == 0)) { c->stop = VC_STOP_BREAK; return i; } }")
    s = s.replace("uint64_t vc_run(VC01* c, uint64_t n) {",
                  "static uint32_t brk[64]; static int nbrk; static int skip1;\n"
                  "void vc_brk_set(uint32_t* a, int n) { nbrk = n; for (int k = 0; k < n; k++) brk[k] = a[k]; }\n"
                  "void vc_brk_skip(int v) { skip1 = v; }\nuint64_t vc_run(VC01* c, uint64_t n) {")
    assert 'vc_brk_set' in s and 'nbrk' in s
    src = os.path.join(WORK, 'vc01core_mp3.c')
    open(src, 'w').write(s)
    subprocess.check_call(['gcc', '-O2', '-shared', '-fPIC', '-I', LLE, '-o', lib, src, srcs[2]])
    return lib

if __name__ == '__main__':
    print(build())
