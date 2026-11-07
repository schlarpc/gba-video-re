//! Differential fuzz of the Rust LZMA against `ads/src/lzma_dec.c` (GBAV_CREF), with the
//! cartridge's fixed lc = 0, lp = 0, pb = 2: random streams, real codebook and index blobs from
//! carts (GBAV_ROMS) and corrupted versions of them, decoded in random slice sizes into the
//! 128 KiB ring (and the 8 KiB blob ring). Compared after every call: the error class (C
//! `error` 1/2) and the whole ring.

#[path = "../../gbav-majesco/tests/support/mod.rs"]
#[macro_use]
mod support;

use gbav_ads::lzma::{Lzma, BLOB_RING, RING};
use support::{CLib, Rng};

type ProbsSize = extern "C" fn(u32, u32) -> usize;
type Init = extern "C" fn(*mut u8, u32, u32, u32, *mut u16, *mut u8, u32, *const u8, *const u8);
type Decode = extern "C" fn(*mut u8, u32) -> i32;

struct C {
    probs_size: ProbsSize,
    init: Init,
    decode: Decode,
    _lib: CLib,
}

fn clib() -> Option<C> {
    let lib = CLib::build("lzmadec", &["ads/src/lzma_dec.c"], &[])?;
    Some(C { probs_size: csym!(lib, "lzma_probs_size"), init: csym!(lib, "lzma_init"), decode: csym!(lib, "lzma_decode"), _lib: lib })
}

/// Decode `input` in the given slices with both decoders; returns (calls, first error code).
fn compare(c: &C, input: &[u8], ring: usize, slices: &[u32], what: &str) -> (usize, i32) {
    let mut st = vec![0u64; 128];
    let mut probs = vec![0u16; (c.probs_size)(0, 0)];
    let mut cring = vec![0u8; ring];
    let r = input.as_ptr_range();
    (c.init)(st.as_mut_ptr() as *mut u8, 0, 0, 2, probs.as_mut_ptr(), cring.as_mut_ptr(), ring as u32 - 1, r.start, r.end);
    let mut d = Lzma::new(input, ring);
    for (k, &n) in slices.iter().enumerate() {
        let ce = (c.decode)(st.as_mut_ptr() as *mut u8, n);
        let re = d.decode(n).err().map_or(0, |e| e.code());
        assert_eq!(ce, re, "{what}: error after call {k} ({n} bytes)");
        assert!(cring == d.ring(), "{what}: ring differs after call {k}");
        if ce != 0 {
            return (k + 1, ce);
        }
    }
    (slices.len(), 0)
}

fn slices(r: &mut Rng, total: u32) -> Vec<u32> {
    let mut v = Vec::new();
    let mut t = 0;
    while t < total && v.len() < 2000 {
        let n = match r.below(4) {
            0 => r.pick(&[2400, 3180, 4240, 4800]) as u32,
            1 => r.range(1, 300) as u32,
            2 => total - t,
            _ => r.range(1, 20000) as u32,
        };
        v.push(n);
        t += n;
    }
    v
}

#[test]
fn lzma_matches_c_on_random_streams() {
    let Some(c) = clib() else { return eprintln!("skip: GBAV_CREF not set") };
    let mut r = Rng::new(21);
    let iters = support::fuzz_iters() * 2;
    let mut errs = [0usize; 3];
    let mut bytes = 0u64;
    for it in 0..iters {
        let n = r.pick_or(&[0, 1, 4, 5, 6, 64, 1000], 5, 40000) as usize;
        let mut input = r.bytes(n);
        if !input.is_empty() && r.chance(0.8) {
            input[0] = 0;
        }
        let ring = if r.chance(0.7) { RING } else { BLOB_RING };
        let total = r.range(1, 300_000) as u32;
        let sl = slices(&mut r, total);
        let (_, e) = compare(&c, &input, ring, &sl, &format!("random iter {it}"));
        errs[e as usize] += 1;
        bytes += total as u64;
    }
    eprintln!("lzma: {iters} random streams agree with lzma_dec.c (ended ok {}, input overrun {}, bad distance {}); up to {bytes} bytes requested", errs[0], errs[1], errs[2]);
}

#[test]
fn lzma_matches_c_on_cart_blobs() {
    let Some(c) = clib() else { return eprintln!("skip: GBAV_CREF not set") };
    let mut blobs: Vec<(Vec<u8>, u32, u32)> = Vec::new();
    for name in ["Super Robot Monkey Team - Hyper Force Go! - Volume 1 (USA)", "Nicktoons Collection - Volume 2 (USA)", "The Adventures of Jimmy Neutron Boy Genius - Volume 1 (USA)"] {
        let Some(rom) = support::load_rom(name) else { return eprintln!("skip: GBAV_ROMS not set") };
        let cart = gbav_ads::AdsCart::open(&rom).unwrap();
        for m in &cart.movies {
            for ch in m.chunks.iter().step_by(7) {
                let a = &rom[ch.a_off..ch.a_off + ch.a_len];
                blobs.push((a[8..].to_vec(), u32::from_le_bytes(a[..4].try_into().unwrap()), 0));
                let b = &rom[ch.b_off..ch.b_off + ch.b_len];
                blobs.push((b[8..].to_vec(), ch.word.frames as u32 * ch.word.mode.blocks() as u32, ch.word.mode.blocks() as u32));
            }
        }
    }
    let mut r = Rng::new(22);
    let iters = support::fuzz_iters();
    let (mut ok, mut bad) = (0, 0);
    for it in 0..iters {
        let (data, size, plane) = &blobs[r.below(blobs.len() as u64) as usize];
        let ring = if *plane > 0 { RING } else { BLOB_RING };
        // the player's pattern (one plane per call) or random slices, exactly `size` bytes
        let mut sl = if *plane > 0 && r.chance(0.5) { vec![*plane; (*size / *plane) as usize] } else { slices(&mut r, *size) };
        let over = sl.iter().sum::<u32>() - *size;
        *sl.last_mut().unwrap() -= over;
        let (_, e) = compare(&c, data, ring, &sl, &format!("cart blob iter {it}"));
        ok += (e == 0) as usize;
        let cor = support::corrupt(&mut r, data, 1);
        let (_, e) = compare(&c, &cor, ring, &sl, &format!("corrupted cart blob iter {it}"));
        bad += (e != 0) as usize;
    }
    eprintln!("lzma: {} real/corrupted blobs agree with lzma_dec.c ({ok} real decoded cleanly, {bad} corrupted ended in an error)", 2 * iters);
}
