#!/usr/bin/env python3
"""Unicorn oracle: run the player's own VX video / audio decoder code on a
CPU-only GBA (uc_gba.py), seeded from an mGBA savestate made by mkstate.py.

Video: each step jumps into the player's main loop just past its "queue
full" wait (0x03007318) and runs until the loop comes back to 0x030072F0.
That iteration calls the frame decoder 0x03006D30(ctx) (plus 0x02000874,
the per-frame stream bookkeeping), which decodes exactly one frame into a
4-slot YUV ring. The display IRQ never runs, so slots are simply reused.

Audio: each step decodes one 512-sample block exactly like the timer-3 IRQ
(0x03007AB0) does: it calls that routine with ctx+0xAC=1 and ctx+0xA0 set
one block ahead, then services the window-refill request (ctx+0xB4) the
way the main loop does (0x0300732C..0x03007358).

  uc_decode.py states/wait_00100.state --frames 50 --out tmp_dir [--verify frames/]
  uc_decode.py states/wait_00100.state --frames 2 --trace trace.csv
  uc_decode.py states/wait_00100.state --audio-blocks 200 --verify-audio audio/
"""
import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from uc_gba import GBA  # noqa: E402
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_CPSR  # noqa: E402

CTX = 0x06013E80
PREROLL = 3  # decoder index = VX frame + 3
LOOP_TOP, AFTER_WAIT = 0x030072F0, 0x03007318
AUDIO_FN = 0x03007AB0
WIN_LO, WIN_HI = 0x08000000, 0x08002000


class Oracle:
    def __init__(self, state, rom=None):
        self.g = GBA(state, **({"rom_path": rom} if rom else {}))
        s = self.g.state
        # page table of the 8 KB matrix window: page -> ROM file offset
        self.pages = list(struct.unpack_from("<16I", s, 0x328))
        self._ml = 0
        self.trace = None

    def ctx(self):
        return struct.unpack("<128I", self.g.read(CTX, 512))

    def _sync_pages(self):
        for paddr, vaddr, size in self.g.matrix_log[self._ml:]:
            for i in range(size >> 9):
                p = (vaddr >> 9) + i
                if p < 16:
                    self.pages[p] = paddr + (i << 9)
        self._ml = len(self.g.matrix_log)

    def enable_trace(self, writer, lo=WIN_LO, hi=WIN_HI):
        """Log every read of the matrix window (= compressed bitstream) with its ROM file offset."""
        self.trace = writer

        def hook(uc, access, addr, size, value, _):
            self._sync_pages()
            val = int.from_bytes(uc.mem_read(addr, size), "little")
            fo = self.pages[(addr - WIN_LO) >> 9] + (addr & 0x1FF)
            writer.writerow([self.cur_tag, f"{uc.reg_read(UC_ARM_REG_PC):08x}", f"{addr:08x}", size,
                             f"{val:0{size * 2}x}", f"{fo:08x}"])
        self.g.uc.hook_add(UC_HOOK_MEM_READ, hook, begin=lo, end=hi - 1)
        writer.writerow(["tag", "pc", "addr", "size", "value", "rom_file_offset"])

    def decode_frame(self):
        """Decode the next video frame. Returns (VX frame index, yuv bytes)."""
        self.cur_tag = f"v{self.ctx()[10] - PREROLL}"
        self.g.run_until(AFTER_WAIT, LOOP_TOP, max_insns=200_000_000)
        self._sync_pages()
        w = self.ctx()
        n = w[10] - 1  # decoder index of the frame just decoded; slot n&3
        return n - PREROLL, self.g.read(w[n & 3], 0xF000)

    def decode_audio_block(self):
        """Decode the next 512-sample audio block. Returns (block index, 512 bytes s8)."""
        g, uc = self.g, self.g.uc
        w = self.ctx()
        j = w[0x29]
        self.cur_tag = f"a{j}"
        uc.mem_write(CTX + 0xAC, struct.pack("<I", 1))
        uc.mem_write(CTX + 0xA0, struct.pack("<I", j + 1))
        g.call(AUDIO_FN, (CTX,), max_insns=50_000_000)
        w = self.ctx()
        assert w[0x29] == j + 1, "audio routine did not decode exactly one block"
        if w[0xB4 // 4]:  # refill request: main loop copies 2 pages from ctx+0x90 to ctx+0xB4
            src, dst = w[0x90 // 4], w[0xB4 // 4]
            m = g.matrix
            m["paddr"], m["vaddr"], m["size"] = src & 0x03FFFFFF, dst & 0x007FFFFF, 2 << 9
            g._hook_matrix(uc, 0, 0x08800180, 4, 1, None)
            uc.mem_write(CTX + 0x90, struct.pack("<I", src + 0x400))
            uc.mem_write(CTX + 0xB4, struct.pack("<I", 0))
            self._sync_pages()
        slot = w[0x8C // 4] + ((j & 15) << 9)
        return j, g.read(slot, 512)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--audio-blocks", type=int, default=0)
    ap.add_argument("--out", help="write NNNNN.yuv / audio_JJJJJJ.s8 here")
    ap.add_argument("--verify", help="ground-truth frames dir (with yuv/) to compare against")
    ap.add_argument("--verify-audio", help="ground-truth audio dir (pcm.s8 + blocks.csv)")
    ap.add_argument("--trace", help="CSV of every bitstream-window read")
    args = ap.parse_args()

    o = Oracle(args.state)
    tf = None
    if args.trace:
        tf = open(args.trace, "w", newline="")
        o.enable_trace(csv.writer(tf))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    ok = bad = 0
    for _ in range(args.frames):
        n, yuv = o.decode_frame()
        if args.out:
            open(os.path.join(args.out, f"{n:05d}.yuv"), "wb").write(yuv)
        if args.verify:
            p = os.path.join(args.verify, "yuv", f"{n:05d}.yuv")
            if os.path.exists(p):
                if open(p, "rb").read() == yuv:
                    ok += 1
                else:
                    bad += 1
                    print(f"frame {n}: MISMATCH")
        if not args.verify:
            print(f"frame {n} decoded", file=sys.stderr)
    if args.verify:
        print(f"video: {ok} match, {bad} mismatch")

    gt = None
    if args.verify_audio:
        rows = list(csv.DictReader(open(os.path.join(args.verify_audio, "blocks.csv"))))
        pcm = open(os.path.join(args.verify_audio, "pcm.s8"), "rb").read()
        gt = {int(r["block"]): pcm[i * 512:(i + 1) * 512] for i, r in enumerate(rows)}
    aok = abad = amiss = 0
    for _ in range(args.audio_blocks):
        j, blk = o.decode_audio_block()
        if args.out:
            open(os.path.join(args.out, f"audio_{j:06d}.s8"), "wb").write(blk)
        if gt is not None:
            if j not in gt:
                amiss += 1
            elif gt[j] == blk:
                aok += 1
            else:
                abad += 1
                print(f"audio block {j}: MISMATCH")
    if gt is not None:
        print(f"audio: {aok} match, {abad} mismatch, {amiss} not in ground truth")
    if tf:
        tf.close()


if __name__ == "__main__":
    main()
