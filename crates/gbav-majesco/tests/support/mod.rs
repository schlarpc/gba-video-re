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
