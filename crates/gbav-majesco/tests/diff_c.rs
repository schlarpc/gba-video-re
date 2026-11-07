//! Differential fuzz of the Rust VQ and ADPCM layers against the lanes' C reference
//! (`majesco-shared/src/{vq,adpcm}.c`, and `sonicx2-proto/src/audio/sx2_adpcm.c` for the
//! prototype's unclipped output stage), on random and corrupted inputs. Skips without GBAV_CREF.

#[path = "support/mod.rs"]
#[macro_use]
mod support;

use gbav_majesco::adpcm::{Adpcm, CtabCoding, Output};
use gbav_majesco::vq::{self, Mode, Row159, Tables, Vcb};
use support::{CLib, Rng};

fn majesco_lib() -> Option<CLib> {
    CLib::build("mjshared", &["majesco-shared/src/vq.c", "majesco-shared/src/adpcm.c"], &[])
}

type Unpredict = extern "C" fn(*mut u8, i32, i32);
type Init = extern "C" fn(*mut u8);
type Convert = extern "C" fn(*const u8, i32, i32, *const u8, *mut u16);
type Blit = extern "C" fn(*const u8, i32, *const u16, *const u16, *mut u16);

#[test]
fn vq_matches_c() {
    let Some(lib) = majesco_lib() else { return eprintln!("skip: GBAV_CREF not set") };
    let c_unpredict: Unpredict = csym!(lib, "mvq_unpredict");
    let c_init: Init = csym!(lib, "mvq_init");
    let c_convert: Convert = csym!(lib, "mvq_convert");
    let c_blit: Blit = csym!(lib, "mvq_blit");
    let mut r = Rng::new(1);
    let iters = support::fuzz_iters() * 4;
    for it in 0..iters {
        let mode = Mode::new(r.below(8) as u32);
        let ncb = if r.chance(0.5) { 256 } else { r.range(1, 256) as usize };
        let mut dither = [0u8; 16];
        for d in &mut dither {
            *d = if r.chance(0.8) { r.below(8) as u8 } else { r.byte() };
        }
        let gain = r.pick_or(&[128, 153, 179, 204, 230, 256, 281, 307, 332, 358, 384, 409], 0, 1023) as i32;
        let bias = r.pick_or(&[100, 100, 151, 49], 0, 200) as i32 - 100;
        let n = mode.codebook_bytes(ncb).max(mode.unpredict_bytes(ncb));
        let cb0 = r.bytes(n);
        // C tables: dither[16], int brightness, int gain_bias, lut[256], five[16][256]
        let mut ct = vec![0u64; 8192 / 8];
        {
            let b = bytemuck(&mut ct);
            b[..16].copy_from_slice(&dither);
            b[16..20].copy_from_slice(&gain.to_le_bytes());
            b[20..24].copy_from_slice(&bias.to_le_bytes());
        }
        c_init(ct.as_mut_ptr() as *mut u8);
        let t = Tables::new(dither, gain, bias);

        let mut c_cb = cb0.clone();
        c_unpredict(c_cb.as_mut_ptr(), mode.id() as i32, ncb as i32);
        let mut r_cb = cb0.clone();
        vq::unpredict(&mut r_cb, mode, ncb).unwrap();
        assert_eq!(c_cb, r_cb, "unpredict iter {it} mode {}", mode.id());

        let fill: Vec<u16> = (0..256 * 16).map(|_| r.next() as u16).collect();
        let mut c_out = fill.clone();
        c_convert(c_cb.as_ptr(), mode.id() as i32, ncb as i32, ct.as_ptr() as *const u8, c_out.as_mut_ptr());
        let mut r_out: Box<Vcb> = Box::new([[0; 16]; 256]);
        for (e, row) in r_out.iter_mut().enumerate() {
            row.copy_from_slice(&fill[e * 16..e * 16 + 16]);
        }
        vq::convert(&r_cb, mode, ncb, &t, &mut r_out).unwrap();
        let r_flat: Vec<u16> = r_out.iter().flatten().copied().collect();
        assert_eq!(c_out, r_flat, "convert iter {it} mode {} ncb {ncb} gain {gain}+{bias}", mode.id());

        let idx = r.bytes(mode.blocks());
        let pal: [u16; 24] = std::array::from_fn(|_| if r.chance(0.5) { 0 } else { r.next() as u16 });
        let fb0: Vec<u16> = (0..240 * 160).map(|_| r.next() as u16).collect();
        let mut c_fb = fb0.clone();
        c_blit(idx.as_ptr(), mode.id() as i32, c_out.as_ptr(), pal.as_ptr(), c_fb.as_mut_ptr());
        let mut r_fb = fb0.clone();
        let lines = vq::blit(&idx, mode, &r_out, Row159::Palette(pal), &mut r_fb).unwrap();
        assert_eq!(lines, 160);
        assert_eq!(c_fb, r_fb, "blit iter {it} mode {}", mode.id());
        // prototype blit: same as the shipped one above the grid, line 159 untouched
        let mut p_fb = fb0.clone();
        let lines = vq::blit(&idx, mode, &r_out, Row159::Untouched, &mut p_fb).unwrap();
        assert_eq!(lines, mode.grid_lines());
        assert_eq!(&c_fb[..lines * 240], &p_fb[..lines * 240]);
        assert_eq!(&fb0[lines * 240..], &p_fb[lines * 240..]);
    }
    eprintln!("vq: {iters} random codebooks/frames agree with vq.c (all 8 modes)");
}

fn bytemuck(v: &mut [u64]) -> &mut [u8] {
    // SAFETY: u64 -> u8 view of the same memory, alignment 1, length scaled.
    unsafe { std::slice::from_raw_parts_mut(v.as_mut_ptr() as *mut u8, v.len() * 8) }
}

type MjInit = extern "C" fn(*mut u8, *const u8, usize, i32);
type MjSeek = extern "C" fn(*mut u8, usize, u32);
type MjDecode = extern "C" fn(*mut u8, *mut i8, usize) -> usize;

/// A random ADPCM-like payload: mostly random bits, with restart headers at some word
/// boundaries and sometimes the continuation bit, so both header kinds and every VLC occur.
fn adpcm_payload(r: &mut Rng) -> Vec<u8> {
    let n = r.pick_or(&[4, 8, 64, 300, 2000, 9000], 4, 20000) as usize & !3;
    let mut v = r.bytes(n.max(4));
    if r.chance(0.8) {
        v[3] &= 0x7f; // first header is a restart (bit 31 of word 0)
    }
    if r.chance(0.3) {
        // small step values keep the predictor in range for longer
        v[2] = 0;
        v[1] = r.below(4) as u8;
    }
    v
}

#[test]
fn adpcm_matches_c() {
    let Some(lib) = majesco_lib() else { return eprintln!("skip: GBAV_CREF not set") };
    let c_init: MjInit = csym!(lib, "mj_adpcm_init");
    let c_seek: MjSeek = csym!(lib, "mj_adpcm_seek");
    let c_dec: MjDecode = csym!(lib, "mj_adpcm_decode");
    let mut r = Rng::new(2);
    let iters = support::fuzz_iters();
    let (mut overruns, mut total) = (0, 0usize);
    for it in 0..iters {
        let data = adpcm_payload(&mut r);
        let coding = if r.chance(0.5) { CtabCoding::Vlc } else { CtabCoding::Flat };
        let mut st = vec![0u64; 64];
        c_init(st.as_mut_ptr() as *mut u8, data.as_ptr(), data.len(), (coding == CtabCoding::Flat) as i32);
        let mut a = Adpcm::new(&data[..], coding, Output::Clip);
        if r.chance(0.2) {
            let off = (r.below(data.len() as u64) as usize) & !3;
            c_seek(st.as_mut_ptr() as *mut u8, off, 3);
            a.seek(off, 3);
        }
        let want = r.range(1, 3 * 16458 + 7) as usize;
        let mut done = 0;
        while done < want {
            let k = (r.pick_or(&[1, 2, 1371, 1372, 4096, 16458], 1, 20000) as usize).min(want - done);
            let mut co = vec![0i8; k];
            let mut ro = vec![0i8; k];
            let cn = c_dec(st.as_mut_ptr() as *mut u8, co.as_mut_ptr(), k);
            let rn = a.decode(&mut ro);
            assert_eq!(cn, rn, "iter {it}: sample count after {done}");
            assert_eq!(co, ro, "iter {it}: samples {done}..{}", done + k);
            done += k;
            total += rn;
            if rn < k {
                overruns += 1;
                break;
            }
        }
    }
    eprintln!("adpcm: {iters} random streams, {total} samples agree with adpcm.c ({overruns} stopped at an overrun, same place)");
}

#[test]
fn adpcm_wrap_matches_sx2() {
    let Some(lib) = CLib::build("sx2adpcm", &["sonicx2-proto/src/audio/sx2_adpcm.c"], &[]) else {
        return eprintln!("skip: GBAV_CREF not set");
    };
    type Parse = extern "C" fn(*const u8, usize, *mut u8) -> i32;
    type Sx2Init = extern "C" fn(*mut u8, *const u8, i32);
    type Sx2Dec = extern "C" fn(*mut u8, *mut i8, u32);
    let c_parse: Parse = csym!(lib, "sx2a_parse");
    let c_init: Sx2Init = csym!(lib, "sx2a_init");
    let c_dec: Sx2Dec = csym!(lib, "sx2a_decode");
    let mut r = Rng::new(3);
    let iters = support::fuzz_iters();
    let mut wrapped = 0usize;
    for it in 0..iters {
        // a type-2 resource: 3 header words (codec 3), last_frames, nblocks, sizes, payload
        let nblocks = r.range(1, 4) as usize;
        let sizes: Vec<u16> = (0..nblocks).map(|_| r.range(200, 3000) as u16).collect();
        let mut res = Vec::new();
        for w in [0xc100_0000u32, 0xcccc_ccc1, 0xcccc_cc00] {
            res.extend_from_slice(&w.to_le_bytes());
        }
        res.extend_from_slice(&12u16.to_le_bytes());
        res.extend_from_slice(&(nblocks as u16).to_le_bytes());
        for s in &sizes {
            res.extend_from_slice(&s.to_le_bytes());
        }
        while res.len() % 4 != 0 {
            res.push(0);
        }
        let h = res.len();
        let mut payload = Vec::new();
        for &s in &sizes {
            let mut b = r.bytes(s as usize * 4);
            b[3] &= 0x7f; // restart header at every block (as on every real stream)
            if r.chance(0.5) {
                b[2] = 0;
                b[1] = r.range(0x60, 0xff) as u8; // large steps: samples leave [-128, 127]
            }
            payload.extend_from_slice(&b);
        }
        res.extend_from_slice(&payload);
        let clip = r.chance(0.3);
        let mut info = vec![0u64; 32];
        assert_eq!(c_parse(res.as_ptr(), res.len(), info.as_mut_ptr() as *mut u8), 0);
        let mut ctx = vec![0u64; 64];
        c_init(ctx.as_mut_ptr() as *mut u8, info.as_ptr() as *const u8, clip as i32);
        let mut a = Adpcm::new(&res[h..], CtabCoding::Flat, if clip { Output::Clip } else { Output::Wrap });
        let n = r.range(1, nblocks as u64 * 16458) as usize;
        let mut ro = vec![0i8; n];
        let rn = a.decode(&mut ro);
        let mut co = vec![0i8; n];
        c_dec(ctx.as_mut_ptr() as *mut u8, co.as_mut_ptr(), n as u32);
        // sx2_adpcm.c reads zeros past the payload instead of stopping: compare what Rust decoded
        assert_eq!(&co[..rn], &ro[..rn], "iter {it} clip {clip}");
        if !clip {
            let mut cl = Adpcm::new(&res[h..], CtabCoding::Flat, Output::Clip);
            let mut co2 = vec![0i8; rn];
            cl.decode(&mut co2);
            wrapped += co2.iter().zip(&ro).filter(|(a, b)| a != b).count();
        }
    }
    eprintln!("adpcm wrap/clip: {iters} random resources agree with sx2_adpcm.c ({wrapped} samples differ between wrap and clip)");
}
