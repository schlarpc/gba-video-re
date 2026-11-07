//! Test support shared by the gbav crates' integration tests.
//!
//! Environment gates (each test that needs one prints a notice and passes without it):
//! - `GBAV_ROMS`: directory of your ROM dumps, named as No-Intro does or by the short ids of
//!   `verify/carts.csv`;
//! - `GBAV_VERIFY`: the `verify/` directory (default: the one in this source tree);
//! - `GBAV_CDIFF=1`: run the differential tests against the C reference decoders in
//!   `reference/` (needs a C compiler, `$CC` or `cc`); `GBAV_CREF` overrides the directory;
//! - `GBAV_FUZZ_ITERS`: iterations per differential fuzz target (default 300);
//! - `GBAV_ONLY=a,b`: restrict a whole-family test to these cart short ids.
//!
//! `CLib` loads a compiled C reference with `dlopen`; that is the only `unsafe` in the workspace.

use std::ffi::{c_char, c_int, c_void, CString};
use std::path::{Path, PathBuf};
use std::process::Command;

pub use gbav_common::manifest::{self, Cart, Known, Manifest, StreamManifests};

/// Repository root (the source tree this crate was built from).
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

pub fn roms_dir() -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os("GBAV_ROMS")?);
    d.is_dir().then_some(d)
}

/// `verify/`, or `None` (with a notice) if it cannot be found.
pub fn verify_dir() -> Option<PathBuf> {
    manifest::verify_dir()
}

/// The carts of `family` in `verify/carts.csv`.
pub fn carts(family: &str) -> Vec<Cart> {
    let Some(d) = verify_dir() else { return Vec::new() };
    manifest::carts(&d).expect("verify/carts.csv").into_iter().filter(|c| c.family == family).collect()
}

/// Known cart-side differences for one stream.
pub fn known(family: &str, short_id: &str, stream: &str) -> Vec<Known> {
    let Some(d) = verify_dir() else { return Vec::new() };
    manifest::known(&d)
        .expect("verify/known.csv")
        .into_iter()
        .filter(|k| k.family == family && k.short_id == short_id && k.stream == stream.replace(' ', "_"))
        .collect()
}

/// Manifest paths of a stream.
pub fn stream_manifests(family: &str, short_id: &str, stream: &str) -> Option<StreamManifests> {
    Some(StreamManifests::new(&verify_dir()?, family, short_id, stream))
}

/// Path of a cart's ROM in `GBAV_ROMS`, by short id or No-Intro name (without `.gba`).
pub fn rom_path(key: &str) -> Option<PathBuf> {
    let dir = roms_dir()?;
    let mut names = vec![key.to_string()];
    if let Some(d) = verify_dir() {
        if let Some(c) = manifest::carts(&d).ok()?.into_iter().find(|c| c.short_id == key || c.nointro == key) {
            names = vec![c.short_id, c.nointro];
        }
    }
    if !key.starts_with("Game Boy Advance Video - ") {
        names.push(format!("Game Boy Advance Video - {key}"));
    }
    names.into_iter().map(|n| dir.join(format!("{n}.gba"))).find(|p| p.is_file())
}

/// A cart's ROM as a shared buffer.
pub fn load_rom(key: &str) -> Option<gbav_common::Rom> {
    Some(std::fs::read(rom_path(key)?).expect("read ROM").into())
}

/// The C reference directory (`reference/` of this tree, or `$GBAV_CREF`) when the C
/// differential tests are enabled with `GBAV_CDIFF=1`.
pub fn cref_dir() -> Option<PathBuf> {
    let on = std::env::var("GBAV_CDIFF").is_ok_and(|v| !v.is_empty() && v != "0");
    let d = match std::env::var_os("GBAV_CREF") {
        Some(p) => PathBuf::from(p),
        None if on => repo_root().join("reference"),
        None => return None,
    };
    d.is_dir().then_some(d)
}

pub fn fuzz_iters() -> usize {
    std::env::var("GBAV_FUZZ_ITERS").ok().and_then(|s| s.parse().ok()).unwrap_or(300)
}

/// `GBAV_ONLY=a,b`: restrict a whole-family test to these cart ids.
pub fn only(id: &str) -> bool {
    match std::env::var("GBAV_ONLY") {
        Ok(v) if !v.is_empty() => v.split(',').any(|x| x == id),
        _ => true,
    }
}

/// Print a skip notice; returns `None` so a test can `let Some(x) = .. else { return skip(..) }`.
pub fn skip(what: &str) {
    eprintln!("skipped: {what}");
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
    /// Compile `sources` (relative to the repository's `reference/` directory) into
    /// `lib<name>.so` under `out_dir`. `None` unless the C differential tests are enabled
    /// ([`cref_dir`]).
    pub fn build(name: &str, sources: &[&str], defines: &[&str], out_dir: &Path) -> Option<CLib> {
        let base = cref_dir()?;
        let out_dir = out_dir.join("cref");
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
#[macro_export]
macro_rules! csym {
    ($lib:expr, $name:expr) => {
        // SAFETY: the declared type matches the C prototype in the reference headers.
        {
            #[allow(clippy::missing_transmute_annotations)]
            let f = unsafe { std::mem::transmute($lib.sym($name)) };
            f
        }
    };
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
