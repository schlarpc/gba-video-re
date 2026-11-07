//! Debug helper: decode one case of a fuzz corpus (tests/fuzz/gen_corpus.py) and print the
//! per-MB start bits and error count after each frame.  vx_fuzz_case ROM_VXPP ROM_VXGB CORPUS CASE
use gbav_vx::{Core, Tables, Variant};

fn rd32(b: &[u8], p: &mut usize) -> u32 {
    let v = u32::from_le_bytes(b[*p..*p + 4].try_into().unwrap());
    *p += 4;
    v
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let pp = std::fs::read(&a[1]).unwrap();
    let gb = std::fs::read(&a[2]).unwrap();
    let b = std::fs::read(&a[3]).unwrap();
    let want: u32 = a[4].parse().unwrap();
    let tabs = [Tables::from_rom(&pp, Variant::VxPP).unwrap(), Tables::from_rom(&gb, Variant::Vxgb).unwrap()];
    let mut p = 8;
    for case in 0.. {
        let (vxgb, nframes) = (b[p] as usize, b[p + 1] as usize);
        p += 2;
        let qp = rd32(&b, &mut p);
        let slen = rd32(&b, &mut p) as usize;
        let stream = &b[p..p + slen];
        p += slen;
        let mut core = Core::new(tabs[vxgb].clone(), 240, 160, qp, stream);
        for _ in 0..rd32(&b, &mut p) {
            let off = rd32(&b, &mut p) as usize;
            let len = rd32(&b, &mut p) as usize;
            core.ew[off..off + len].copy_from_slice(&b[p..p + len]);
            p += len;
        }
        p += 24;
        if case != want {
            continue;
        }
        core.mb_bits = Some(Vec::new());
        core.trace = Some(Vec::new());
        for f in 0..nframes {
            core.decode_frame(stream);
            println!("frame {f} err {} mb_bits {:?}", core.stats.err, core.mb_bits.as_ref().unwrap());
            for t in core.trace.as_mut().unwrap().drain(..) {
                println!("  blk {}x{} @{:05x} mode {} bit {}", t[0], t[1], t[2], t[3], t[4]);
            }
        }
        std::fs::write("/tmp/claude-1000/case.rust.ewram", &core.ew[..]).ok();
        break;
    }
}
