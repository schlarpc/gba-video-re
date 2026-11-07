import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'vx', 'container'))
import vxdemux, vxvwalk
rom = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'shrek.gba'), 'rb').read()
c = vxdemux.VXContainer(rom)
sw = vxvwalk.swap16(bytes(c.video))
lut = rom[vxvwalk.LUT_ROM:vxvwalk.LUT_ROM + vxvwalk.LUT_SIZE]
tail = c.video_tail_bits()
allstarts, allq, bad = [], [], 0
t = time.time()
for s in c.segments():
    st, q = vxvwalk.walk_segment(sw, lut, s[3], s[2], c.width, c.height)
    exp = s[4]
    if exp is None:
        ok = st[-1] <= tail and tail - st[-1] < 16 * 8
        print('last segment end', st[-1], 'tail', tail, 'video bits', len(c.video) * 8)
    else:
        ok = st[-1] == exp
    if not ok:
        bad += 1; print('SEG MISMATCH', s, st[-1])
    allstarts += st[:-1]; allq += q
allstarts.append(st[-1])
print('segments', len(c.segments()), 'bad', bad, 'frames', len(allq), 'time %.1f' % (time.time() - t))
json.dump({'frame_start_bits': allstarts, 'frame_qp_field': allq}, open('frame_bits.json', 'w'))
