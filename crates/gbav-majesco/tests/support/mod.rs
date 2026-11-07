//! Test support shared by the Majesco crates' integration tests (included with `#[path]`):
//! environment gates, a small PRNG, and the C reference decoders built as shared libraries.
//!
//! Gates:
//! - `GBAV_ROMS`: directory of No-Intro named ROMs (tests that need ROMs skip without it);
//! - `GBAV_CREF`: the suite directory holding the lanes' C reference code
//!   (`majesco-shared/`, `hydrogen/`, `ads/`, `sonicx2-proto/`); differential tests skip without it.
//! - `GBAV_FUZZ_ITERS`: iterations per differential fuzz target (default 300).
#![allow(dead_code)]

use std::ffi::{c_char, c_int, c_void, CString};
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn roms_dir() -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os("GBAV_ROMS")?);
    d.is_dir().then_some(d)
}

pub fn cref_dir() -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os("GBAV_CREF")?);
    d.is_dir().then_some(d)
}

pub fn fuzz_iters() -> usize {
    std::env::var("GBAV_FUZZ_ITERS").ok().and_then(|s| s.parse().ok()).unwrap_or(300)
}

pub fn rom_path(name: &str) -> Option<PathBuf> {
    let p = roms_dir()?.join(format!("Game Boy Advance Video - {name}.gba"));
    p.is_file().then_some(p)
}

pub fn load_rom(name: &str) -> Option<Vec<u8>> {
    Some(std::fs::read(rom_path(name)?).expect("read ROM"))
}

/// xorshift64*; deterministic per seed.
pub struct Rng(u64);
impl Rng {
    pub fn new(seed: u64) -> Rng {
        Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }
    pub fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            0
        } else {
            self.next() % n
        }
    }
    pub fn range(&mut self, lo: u64, hi: u64) -> u64 {
        lo + self.below(hi - lo + 1)
    }
    pub fn byte(&mut self) -> u8 {
        self.next() as u8
    }
    pub fn bytes(&mut self, n: usize) -> Vec<u8> {
        (0..n).map(|_| self.byte()).collect()
    }
    pub fn chance(&mut self, p: f64) -> bool {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64 <= p
    }
    /// One of `v`, or (with probability 1/(len+1)) a value in lo..=hi.
    pub fn pick_or(&mut self, v: &[u64], lo: u64, hi: u64) -> u64 {
        let i = self.below(v.len() as u64 + 1) as usize;
        if i == v.len() {
            self.range(lo, hi)
        } else {
            v[i]
        }
    }
    pub fn pick<T: Copy>(&mut self, v: &[T]) -> T {
        v[self.below(v.len() as u64) as usize]
    }
}

/// Flip random bits / truncate / splice garbage into a copy of `b`.
pub fn corrupt(r: &mut Rng, b: &[u8], keep_prefix: usize) -> Vec<u8> {
    let mut v = b.to_vec();
    if v.len() <= keep_prefix {
        return v;
    }
    match r.below(4) {
        0 => {
            for _ in 0..r.range(1, 8) {
                let i = keep_prefix + r.below((v.len() - keep_prefix) as u64) as usize;
                v[i] ^= 1 << r.below(8);
            }
        }
        1 => {
            let n = keep_prefix + r.below((v.len() - keep_prefix) as u64) as usize;
            v.truncate(n);
        }
        2 => {
            let i = keep_prefix + r.below((v.len() - keep_prefix) as u64) as usize;
            let n = (r.range(1, 64) as usize).min(v.len() - i);
            for b in &mut v[i..i + n] {
                *b = r.byte();
            }
        }
        _ => {
            let i = keep_prefix + r.below((v.len() - keep_prefix) as u64) as usize;
            v[i] = r.byte();
        }
    }
    v
}

// ---------------------------------------------------------------- C reference as a shared lib

extern "C" {
    fn dlopen(filename: *const c_char, flag: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlerror() -> *const c_char;
}
const RTLD_NOW: c_int = 2;
const RTLD_LOCAL: c_int = 0;

/// A C reference library compiled from the lanes' sources and loaded with dlopen. Symbols are
/// looked up by name and transmuted to the matching `extern "C"` fn type by the caller.
pub struct CLib {
    handle: *mut c_void,
    pub path: PathBuf,
}
// the handle is only used to look symbols up; the C code keeps no shared mutable state that the
// tests touch from several threads (fixed-table init in hyinflate.c runs once, before threads)
unsafe impl Send for CLib {}
unsafe impl Sync for CLib {}

impl CLib {
    /// Compile `sources` (relative to the GBAV_CREF dir) into `lib<name>.so` in the test tmpdir.
    pub fn build(name: &str, sources: &[&str], defines: &[&str]) -> Option<CLib> {
        let base = cref_dir()?;
        let out_dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("cref");
        std::fs::create_dir_all(&out_dir).ok()?;
        let out = out_dir.join(format!("lib{name}.so"));
        let mut cmd = Command::new(std::env::var("CC").unwrap_or_else(|_| "cc".into()));
        cmd.args(["-O2", "-std=c99", "-D_POSIX_C_SOURCE=200809L", "-fPIC", "-shared", "-w", "-o"]).arg(&out);
        for d in defines {
            cmd.arg(format!("-D{d}"));
        }
        for s in sources {
            let p = base.join(s);
            assert!(p.is_file(), "C reference source missing: {}", p.display());
            cmd.arg(p);
        }
        let st = cmd.status().expect("run the C compiler");
        assert!(st.success(), "C reference build failed: {name}");
        Some(CLib::open(&out))
    }

    pub fn open(path: &Path) -> CLib {
        let c = CString::new(path.to_str().unwrap()).unwrap();
        // SAFETY: plain dlopen of a library we just built; no initialisers with side effects.
        let h = unsafe { dlopen(c.as_ptr(), RTLD_NOW | RTLD_LOCAL) };
        if h.is_null() {
            let e = unsafe { std::ffi::CStr::from_ptr(dlerror()) };
            panic!("dlopen {}: {:?}", path.display(), e);
        }
        CLib { handle: h, path: path.to_owned() }
    }

    /// Raw symbol address; callers transmute to the right fn pointer type.
    pub fn sym(&self, name: &str) -> *mut c_void {
        let c = CString::new(name).unwrap();
        // SAFETY: dlsym on a live handle.
        let p = unsafe { dlsym(self.handle, c.as_ptr()) };
        assert!(!p.is_null(), "symbol {name} not found in {}", self.path.display());
        p
    }
}

/// `let f: extern "C" fn(..) -> .. = csym!(lib, "name");`
macro_rules! csym {
    ($lib:expr, $name:expr) => {
        // SAFETY: the declared type matches the C prototype in the reference headers.
        unsafe { std::mem::transmute($lib.sym($name)) }
    };
}

// ---------------------------------------------------------------- manifests

/// Rows of a gzipped CSV manifest (header dropped), or None if the file does not exist.
/// Decompressed with the system `gzip` (no crates.io in the workspace).
pub fn read_manifest(path: &Path) -> Option<Vec<Vec<String>>> {
    if !path.is_file() {
        return None;
    }
    let out = Command::new("gzip").arg("-dc").arg(path).output().expect("run gzip");
    assert!(out.status.success(), "gzip -dc {}", path.display());
    let text = String::from_utf8(out.stdout).expect("utf8 manifest");
    Some(text.lines().skip(1).filter(|l| !l.is_empty()).map(|l| l.split(',').map(str::to_owned).collect()).collect())
}

/// Lowercase hex MD5 (a streaming implementation: gbav_common::md5 allocates per 64-byte block,
/// too slow for ~80 GB of framebuffers). Cross-checked against gbav_common::md5 in `md5_agrees`.
pub fn md5_hex(b: &[u8]) -> String {
    md5(b).iter().map(|x| format!("{x:02x}")).collect()
}

pub fn md5(data: &[u8]) -> [u8; 16] {
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14,
        20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6,
        10, 15, 21,
    ];
    static K: std::sync::OnceLock<[u32; 64]> = std::sync::OnceLock::new();
    let k = K.get_or_init(|| std::array::from_fn(|i| ((i as f64 + 1.0).sin().abs() * 4294967296.0) as u32));
    let mut h: [u32; 4] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
    let mut block = |chunk: &[u8]| {
        let m: [u32; 16] = std::array::from_fn(|i| u32::from_le_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap()));
        let (mut a, mut b, mut c, mut d) = (h[0], h[1], h[2], h[3]);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let t = d;
            d = c;
            c = b;
            b = b.wrapping_add(a.wrapping_add(f).wrapping_add(k[i]).wrapping_add(m[g]).rotate_left(S[i]));
            a = t;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
    };
    let full = data.len() / 64 * 64;
    for c in data[..full].chunks_exact(64) {
        block(c);
    }
    let mut tail = data[full..].to_vec();
    tail.push(0x80);
    while tail.len() % 64 != 56 {
        tail.push(0);
    }
    tail.extend_from_slice(&((data.len() as u64).wrapping_mul(8)).to_le_bytes());
    for c in tail.chunks_exact(64) {
        block(c);
    }
    let mut out = [0u8; 16];
    for (i, v) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&v.to_le_bytes());
    }
    out
}

/// Run `f` over `items` on all cores; results in input order.
pub fn par_map<T: Sync, R: Send>(items: &[T], f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let n = std::thread::available_parallelism().map_or(4, |n| n.get()).min(items.len().max(1));
    let next = std::sync::atomic::AtomicUsize::new(0);
    let slots: Vec<std::sync::Mutex<Option<R>>> = items.iter().map(|_| std::sync::Mutex::new(None)).collect();
    std::thread::scope(|s| {
        for _ in 0..n {
            s.spawn(|| loop {
                let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                if i >= items.len() {
                    break;
                }
                let r = f(&items[i]);
                *slots[i].lock().unwrap() = Some(r);
            });
        }
    });
    slots.into_iter().map(|m| m.into_inner().unwrap().unwrap()).collect()
}

/// `GBAV_ONLY=a,b`: restrict a whole-family test to these cart ids.
pub fn only(id: &str) -> bool {
    match std::env::var("GBAV_ONLY") {
        Ok(v) if !v.is_empty() => v.split(',').any(|x| x == id),
        _ => true,
    }
}
