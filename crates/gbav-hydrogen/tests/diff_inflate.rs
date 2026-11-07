//! Differential fuzz of the Rust Inflate against `hydrogen/src/hyinflate.c` (GBAV_CREF):
//! random streams from a test encoder (a port of `hydrogen/src/hyenc.py`: stored blocks at every
//! alignment and parity, fixed blocks, dynamic blocks with all code-length codes, all length and
//! distance codes), corrupted versions of them, and (with GBAV_ROMS) real and corrupted blobs
//! from a cart, each decoded in random request sizes. Compared per request: the returned offset,
//! the error class (C `err` 1/2/3), and the output bytes; plus the end-marker check.

#[path = "../../gbav-majesco/tests/support/mod.rs"]
#[macro_use]
mod support;

use gbav_hydrogen::inflate::Inflate;
use support::{CLib, Rng};

const LBASE: [u32; 29] =
    [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
const LEXT: [u32; 29] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
const DBASE: [u32; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
    8193, 12289, 16385, 24577,
];
const DEXT: [u32; 30] = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
const CLORD: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

/// bit writer: MSB first into 16-bit little-endian words
#[derive(Default)]
struct Bw {
    words: Vec<u16>,
    acc: u32,
    n: u32,
}
impl Bw {
    fn put(&mut self, v: u32, k: u32) {
        for i in (0..k).rev() {
            self.acc = (self.acc << 1) | ((v >> i) & 1);
            self.n += 1;
            if self.n == 16 {
                self.words.push(self.acc as u16);
                self.acc = 0;
                self.n = 0;
            }
        }
    }
    fn pos(&self) -> usize {
        self.words.len() * 16 + self.n as usize
    }
    fn data(&self) -> Vec<u8> {
        let mut w = self.words.clone();
        if self.n > 0 {
            w.push((self.acc << (16 - self.n)) as u16);
        }
        w.iter().flat_map(|x| x.to_le_bytes()).collect()
    }
}

fn canon(len: &[u32]) -> Vec<(u32, u32)> {
    let mx = *len.iter().max().unwrap_or(&0) as usize;
    let mut bl = vec![0u32; mx + 2];
    for &l in len {
        if l > 0 {
            bl[l as usize] += 1;
        }
    }
    let mut next = vec![0u32; mx + 2];
    let mut code = 0;
    for b in 1..=mx {
        code = (code + bl[b - 1]) << 1;
        next[b] = code;
    }
    len.iter()
        .map(|&l| {
            if l == 0 {
                (0, 0)
            } else {
                let c = next[l as usize];
                next[l as usize] += 1;
                (c, l)
            }
        })
        .collect()
}

/// Huffman code lengths from frequencies, limited by halving frequencies until they fit.
fn huff_lengths(freq: &[u32], limit: u32) -> Vec<u32> {
    let mut f: Vec<u64> = freq.iter().map(|&x| x as u64).collect();
    let syms: Vec<usize> = (0..f.len()).filter(|&i| f[i] > 0).collect();
    let mut l = vec![0u32; f.len()];
    if syms.len() == 1 {
        l[syms[0]] = 1;
        return l;
    }
    loop {
        let mut nodes: Vec<(u64, Vec<usize>)> = syms.iter().map(|&s| (f[s], vec![s])).collect();
        let mut depth = vec![0u32; f.len()];
        while nodes.len() > 1 {
            nodes.sort_by(|a, b| b.0.cmp(&a.0));
            let a = nodes.pop().unwrap();
            let b = nodes.pop().unwrap();
            let mut m = a.1;
            m.extend(b.1);
            for &s in &m {
                depth[s] += 1;
            }
            nodes.push((a.0 + b.0, m));
        }
        if syms.iter().all(|&s| depth[s] <= limit) {
            for &s in &syms {
                l[s] = depth[s];
            }
            return l;
        }
        for x in &mut f {
            if *x > 0 {
                *x = (*x).div_ceil(2) + 1;
            }
        }
    }
}

fn lcode(n: u32) -> (usize, u32) {
    if n == 258 {
        return (28, 0);
    }
    for i in (0..28).rev() {
        if n >= LBASE[i] && n - LBASE[i] < (1 << LEXT[i]) {
            return (i, n - LBASE[i]);
        }
    }
    unreachable!()
}
fn dcode(d: u32) -> (usize, u32) {
    for i in (0..30).rev() {
        if d >= DBASE[i] {
            return (i, d - DBASE[i]);
        }
    }
    unreachable!()
}

enum Tok {
    Lit(u8),
    Match(u32, u32),
}

fn gen_tokens(r: &mut Rng, out: &mut Vec<u8>, mut n: usize) -> Vec<Tok> {
    let mut toks = Vec::new();
    while n > 0 {
        if !out.is_empty() && n >= 3 && r.chance(0.5) {
            let l = (r.pick_or(&[3, 4, 5, 10, 11, 18, 35, 67, 130, 257, 258], 3, 258) as usize).min(n) as u32;
            let mut d = r.range(1, out.len().min(32768) as u64) as u32;
            if r.chance(0.3) {
                d = (out.len() as u32).min(r.pick(&[1, 2, 5, 7, 24577, 32768]));
            }
            for _ in 0..l {
                let b = out[out.len() - d as usize];
                out.push(b);
            }
            toks.push(Tok::Match(l, d));
            n -= l as usize;
        } else {
            let b = r.pick_or(&[0, 255, 0x41], 0, 255) as u8;
            out.push(b);
            toks.push(Tok::Lit(b));
            n -= 1;
        }
    }
    toks
}

/// A valid stream of `size` bytes; returns (blob, expected output).
fn encode(r: &mut Rng, size: usize, types: &[u32]) -> (Vec<u8>, Vec<u8>) {
    let mut bw = Bw::default();
    let mut out = Vec::new();
    while out.len() < size {
        let bt = r.pick(types);
        let n = (size - out.len()).min(r.pick_or(&[1, 2, 3, 7, 100, 1000], 1, 5000) as usize);
        bw.put(bt, 2);
        if bt == 0 {
            let n = n.min(0xffff);
            let payload = r.bytes(n);
            bw.put(n as u32, 16);
            let mut pending = (16 - bw.pos() % 16) % 16;
            let mut i = 0;
            if n > 0 && pending >= 8 {
                bw.put(payload[0] as u32, 8);
                i = 1;
                pending -= 8;
            }
            bw.put(0, pending as u32);
            let rest = &payload[i..];
            let ev = rest.len() & !1;
            for k in (0..ev).step_by(2) {
                bw.put(rest[k + 1] as u32, 8);
                bw.put(rest[k] as u32, 8);
            }
            if rest.len() & 1 == 1 {
                bw.put(*rest.last().unwrap() as u32, 8);
            }
            out.extend_from_slice(&payload);
            continue;
        }
        let toks = gen_tokens(r, &mut out, n);
        let (lit, dist): (Vec<(u32, u32)>, Option<Vec<(u32, u32)>>) = if bt == 1 {
            let mut l = vec![8u32; 144];
            l.extend(vec![9; 112]);
            l.extend(vec![7; 24]);
            l.extend(vec![8; 8]);
            (canon(&l), None)
        } else {
            let mut lf = vec![0u32; 286];
            let mut df = vec![0u32; 30];
            lf[256] = 1;
            for t in &toks {
                match *t {
                    Tok::Lit(b) => lf[b as usize] += 1,
                    Tok::Match(l, d) => {
                        lf[257 + lcode(l).0] += 1;
                        df[dcode(d).0] += 1;
                    }
                }
            }
            for _ in 0..r.below(21) {
                let i = r.below(286) as usize;
                lf[i] += r.below(4) as u32;
            }
            if df.iter().all(|&x| x == 0) {
                df[r.below(30) as usize] = 1;
            }
            if df.iter().filter(|&&x| x > 0).count() == 1 {
                let i = df.iter().position(|&x| x > 0).unwrap();
                df[(i + 1) % 30] += 1;
            }
            let ll = huff_lengths(&lf, 15);
            let dl = huff_lengths(&df, 15);
            let hlit = 257.max(ll.iter().rposition(|&x| x > 0).unwrap() + 1);
            let hdist = 1.max(dl.iter().rposition(|&x| x > 0).unwrap() + 1);
            let seq: Vec<u32> = ll[..hlit].iter().chain(dl[..hdist].iter()).copied().collect();
            let mut rl: Vec<(u32, u32)> = Vec::new();
            let mut i = 0;
            while i < seq.len() {
                let v = seq[i];
                let mut j = i;
                while j < seq.len() && seq[j] == v {
                    j += 1;
                }
                let mut run = j - i;
                if v == 0 && run >= 3 {
                    while run >= 11 {
                        let k = run.min(138);
                        rl.push((18, (k - 11) as u32));
                        run -= k;
                        i += k;
                    }
                    if run >= 3 {
                        rl.push((17, (run - 3) as u32));
                        i += run;
                        run = 0;
                    }
                    while run > 0 {
                        rl.push((0, 0));
                        run -= 1;
                        i += 1;
                    }
                } else if run >= 4 && r.chance(0.8) {
                    rl.push((v, 0));
                    i += 1;
                    run -= 1;
                    while run >= 3 {
                        let k = run.min(6);
                        rl.push((16, (k - 3) as u32));
                        run -= k;
                        i += k;
                    }
                    while run > 0 {
                        rl.push((v, 0));
                        run -= 1;
                        i += 1;
                    }
                } else {
                    rl.push((v, 0));
                    i += 1;
                }
            }
            let mut cf = vec![0u32; 19];
            for t in &rl {
                cf[t.0 as usize] += 1;
            }
            if cf.iter().filter(|&&x| x > 0).count() == 1 {
                let i = cf.iter().position(|&x| x > 0).unwrap();
                cf[(i + 1) % 19] += 1;
            }
            let cl = huff_lengths(&cf, 7);
            let hclen = 4.max((0..19).filter(|&k| cl[CLORD[k]] > 0).max().unwrap() + 1);
            bw.put((hlit - 257) as u32, 5);
            bw.put((hdist - 1) as u32, 5);
            bw.put((hclen - 4) as u32, 4);
            for k in 0..hclen {
                bw.put(cl[CLORD[k]], 3);
            }
            let cc = canon(&cl);
            for &(s, e) in &rl {
                let (c, l) = cc[s as usize];
                bw.put(c, l);
                match s {
                    16 => bw.put(e, 2),
                    17 => bw.put(e, 3),
                    18 => bw.put(e, 7),
                    _ => {}
                }
            }
            (canon(&ll), Some(canon(&dl)))
        };
        for t in &toks {
            match *t {
                Tok::Lit(b) => {
                    let (c, l) = lit[b as usize];
                    bw.put(c, l);
                }
                Tok::Match(len, d) => {
                    let (li, le) = lcode(len);
                    let (c, l) = lit[257 + li];
                    bw.put(c, l);
                    bw.put(le, LEXT[li]);
                    let (di, de) = dcode(d);
                    match &dist {
                        None => bw.put(di as u32, 5),
                        Some(dt) => {
                            let (c, l) = dt[di];
                            bw.put(c, l);
                        }
                    }
                    bw.put(de, DEXT[di]);
                }
            }
        }
        let (c, l) = lit[256];
        bw.put(c, l);
    }
    bw.put(3, 2);
    let mut blob = (out.len() as u32).to_le_bytes().to_vec();
    blob.extend(bw.data());
    (blob, out)
}

type RunChunks = extern "C" fn(*const u8, usize, *mut u8, usize, *const i64, i32, *mut i64) -> i64;
type HyInit = extern "C" fn(*mut u8, *const u8, usize, *mut u8, usize);
type HyRequest = extern "C" fn(*mut u8, i64) -> i64;
type HyCheckEnd = extern "C" fn(*mut u8) -> i32;

struct C {
    run: RunChunks,
    init: HyInit,
    request: HyRequest,
    check_end: HyCheckEnd,
    _lib: CLib,
}

fn clib() -> Option<C> {
    let lib = CLib::build("hyinflate", &["hydrogen/src/hyinflate.c"], &[])?;
    Some(C {
        run: csym!(lib, "hyinf_run_chunks"),
        init: csym!(lib, "hyinf_init"),
        request: csym!(lib, "hyinf_request"),
        check_end: csym!(lib, "hyinf_check_end"),
        _lib: lib,
    })
}

#[derive(Debug, PartialEq, Eq)]
struct Outcome {
    /// offsets of the requests that succeeded
    offs: Vec<i64>,
    /// bytes written, or -err
    ret: i64,
    out: Vec<u8>,
}

fn c_run(c: &C, blob: &[u8], cap: usize, chunks: &[i64]) -> Outcome {
    let mut out = vec![0u8; cap];
    let mut offs = vec![0i64; chunks.len()];
    let ret = (c.run)(blob.as_ptr(), blob.len(), out.as_mut_ptr(), cap, chunks.as_ptr(), chunks.len() as i32, offs.as_mut_ptr());
    Outcome { offs, ret, out }
}

static QUIRK1: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
static QUIRK2: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

fn rust_run(blob: &[u8], cap: usize, chunks: &[i64]) -> (Outcome, usize) {
    let mut s = Inflate::new(blob, cap);
    let r = rust_run_on(&mut s, chunks);
    let q = s.quirks();
    use std::sync::atomic::Ordering::Relaxed;
    QUIRK1.fetch_add((q.stored_inherits_request > 0) as usize, Relaxed);
    QUIRK2.fetch_add((q.stored_reheader > 0) as usize, Relaxed);
    r
}

fn rust_run_on(s: &mut Inflate<&[u8]>, chunks: &[i64]) -> (Outcome, usize) {
    let mut offs = Vec::new();
    for &n in chunks {
        match s.request(n) {
            Ok(o) => offs.push(o),
            Err(e) => {
                let n = offs.len();
                return (Outcome { offs, ret: -(e.code() as i64), out: s.output().to_vec() }, n);
            }
        }
    }
    let ret = s.produced() as i64;
    let n = offs.len();
    (Outcome { offs, ret, out: s.output().to_vec() }, n)
}

fn compare(c: &C, blob: &[u8], cap: usize, chunks: &[i64], what: &str) -> i64 {
    let co = c_run(c, blob, cap, chunks);
    let (ro, n_ok) = rust_run(blob, cap, chunks);
    assert_eq!(co.ret, ro.ret, "{what}: result (bytes or -err), chunks {chunks:?}");
    // (after an error the C helper has also recorded the failing request's offset)
    assert_eq!(&co.offs[..n_ok], &ro.offs[..], "{what}: request offsets");
    if co.ret >= 0 {
        assert_eq!(co.out, ro.out, "{what}: output bytes");
    } else {
        // after an error the C buffer holds what was written before it; same for Rust
        assert_eq!(co.out, ro.out, "{what}: output bytes before the error");
    }
    // end-marker check after a whole-blob request
    let mut st = vec![0u64; 4096];
    let mut cout = vec![0u8; cap];
    (c.init)(st.as_mut_ptr() as *mut u8, blob.as_ptr(), blob.len(), cout.as_mut_ptr(), cap);
    let size = if blob.len() >= 4 { u32::from_le_bytes(blob[..4].try_into().unwrap()) as i64 } else { 0 };
    (c.request)(st.as_mut_ptr() as *mut u8, size);
    let cend = (c.check_end)(st.as_mut_ptr() as *mut u8) != 0;
    let mut rs = Inflate::new(blob, cap);
    let _ = rs.request(size);
    assert_eq!(cend, rs.check_end(), "{what}: end-marker check");
    co.ret
}

fn chunks_for(r: &mut Rng, size: usize) -> Vec<i64> {
    let mode = r.below(5);
    let mut v = Vec::new();
    let mut tot = 0i64;
    while tot < size as i64 + 10 && v.len() < 4000 {
        let k = match mode {
            0 => size as i64,
            1 => r.pick(&[2400, 3180, 4240, 4800]) as i64,
            2 => r.range(1, 8) as i64,
            3 => r.range(1, 5000) as i64,
            _ => r.pick_or(&[1, 2, 3, 258, 259, 1000], 0, 3000) as i64 - if r.chance(0.05) { 3000 } else { 0 },
        };
        v.push(k);
        tot += k.max(1);
        if mode == 0 {
            break;
        }
    }
    v
}

/// No C needed: streams without stored blocks decode to the encoder's input in any request
/// pattern, and the end marker (block type 3) is where the player expects it.
#[test]
fn inflate_round_trips_encoded_streams() {
    let mut r = Rng::new(10);
    for it in 0..200 {
        let size = r.range(1, 30000) as usize;
        let types: &[u32] = if it % 2 == 0 { &[1, 2] } else { &[2] };
        let (blob, expect) = encode(&mut r, size, types);
        let mut s = Inflate::new(&blob[..], expect.len());
        let mut got = Vec::new();
        let g = r.pick(&[2400u64, 3180, 4240, 4800]) as usize;
        let mut done = 0;
        while done < expect.len() {
            let n = g.min(expect.len() - done);
            let off = s.request(n as i64).unwrap() as usize;
            got.extend_from_slice(&s.output()[off..off + n]);
            done += n;
        }
        assert_eq!(got, expect, "iter {it}");
        assert!(s.check_end(), "iter {it}: end marker");
        assert_eq!(gbav_hydrogen::inflate::decode_blob(&blob).unwrap(), expect);
    }
}

#[test]
fn inflate_matches_c_on_encoded_streams() {
    let Some(c) = clib() else { return eprintln!("skip: GBAV_CREF not set") };
    let mut r = Rng::new(11);
    let iters = support::fuzz_iters() * 3;
    let mut stats = [0usize; 4]; // ok, err1, err2, err3
    let mut exact = 0;
    for it in 0..iters {
        let types: &[u32] = match it % 4 {
            0 => &[0, 1, 2],
            1 => &[0],
            2 => &[1, 2],
            _ => &[2],
        };
        let size = r.pick_or(&[1, 2, 3, 100, 2400, 4800, 20000], 1, 60000) as usize;
        let (blob, expect) = encode(&mut r, size, types);
        let cap = expect.len() + r.pick(&[0usize, 0, 64, 1000]);
        let chunks = chunks_for(&mut r, expect.len());
        let ret = compare(&c, &blob, cap, &chunks, &format!("encoded iter {it}"));
        if ret >= 0 {
            stats[0] += 1;
            if types == [2] || types == [1, 2] {
                // without stored blocks the whole-blob output is the encoder's input
                let mut s = Inflate::new(&blob[..], expect.len());
                s.request(expect.len() as i64).unwrap();
                assert_eq!(&s.output()[..expect.len()], &expect[..], "encoded iter {it}: content");
                exact += 1;
            }
        } else {
            stats[(-ret) as usize] += 1;
        }
        // corrupted copy
        let bad = support::corrupt(&mut r, &blob, 4);
        let ret = compare(&c, &bad, cap, &chunks, &format!("corrupted iter {it}"));
        stats[if ret >= 0 { 0 } else { (-ret) as usize }] += 1;
    }
    eprintln!(
        "inflate: {} streams ({} encoded + corrupted), outcomes ok {} / err1 {} / err2 {} / err3 {}; {exact} round-trips exact; \
         stored-block quirk 1 in {} runs, quirk 2 in {}",
        2 * iters, iters, stats[0], stats[1], stats[2], stats[3],
        QUIRK1.load(std::sync::atomic::Ordering::Relaxed), QUIRK2.load(std::sync::atomic::Ordering::Relaxed)
    );
}

#[test]
fn inflate_matches_c_on_cart_blobs() {
    let Some(c) = clib() else { return eprintln!("skip: GBAV_CREF not set") };
    let Some(rom) = support::load_rom("All Grown Up! - Volume 1 (USA)") else { return eprintln!("skip: GBAV_ROMS not set") };
    let cart = gbav_hydrogen::Cart::open(&rom).unwrap();
    let mut r = Rng::new(12);
    let segs: Vec<_> = cart.streams.iter().flat_map(|s| s.segments.iter().cloned()).collect();
    let iters = support::fuzz_iters();
    let mut n = 0;
    for it in 0..iters {
        let g = &segs[r.below(segs.len() as u64) as usize];
        let (off, len) = if r.chance(0.3) { (g.cb_off, g.cb_len) } else { (g.idx_off, g.idx_len) };
        let blob = &rom[off..off + len];
        let size = u32::from_le_bytes(blob[..4].try_into().unwrap()) as usize;
        let nb = g.word.mode.blocks() as i64;
        let chunks: Vec<i64> = if r.chance(0.5) { vec![nb; g.word.frames as usize] } else { chunks_for(&mut r, size) };
        compare(&c, blob, size, &chunks, &format!("cart blob iter {it}"));
        let bad = support::corrupt(&mut r, blob, 4);
        compare(&c, &bad, size, &chunks, &format!("corrupted cart blob iter {it}"));
        n += 2;
    }
    eprintln!("inflate: {n} real/corrupted All Grown Up blobs agree with hyinflate.c in random request sizes");
}
