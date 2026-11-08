//! Differential fuzzing of the Rust AAC decoder against the C reference
//! (`reference/playyan/aac`), with `GBAV_CDIFF=1` and `GBAV_CREF` pointing at the `reference/`
//! directory that holds `playyan/`. Inputs: Nintendo's MP4 sample (`PY_MEDIA`) and every
//! .m4a/.mp4 in `PY_AAC_CORPUS`; frames are decoded in order with random corruptions
//! (gbav_testkit::corrupt), and both decoders must agree on the outcome (samples reported,
//! firmware error code, or "outside the model") and on every PCM sample. The C reference itself
//! is checked against the firmware in the emulator by tools/playyan/aac/cmp_c.py and tools/playyan/aac/fuzz.py.

use gbav_playyan::aac::Decoder;
use gbav_playyan::mp4::Mp4;
use gbav_playyan::{Firmware, ImageId};
use gbav_testkit::{corrupt, csym, CLib, Rng};
use std::ffi::c_void;
use std::path::PathBuf;

type OpenFn = extern "C" fn(*const u8, usize, i32) -> *mut c_void;
type InitFn = extern "C" fn(*mut c_void, *const u8, usize) -> i32;
type FrameFn = extern "C" fn(*mut c_void, *const u8, usize, *mut i16) -> i32;
type ErrFn = extern "C" fn(*mut c_void) -> i32;
type CloseFn = extern "C" fn(*mut c_void);

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

#[test]
fn aac_fuzz_vs_c() {
    let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    let Some(lib) = CLib::build("pyaac", &["playyan/aac/pyaac.c"], &[], &out) else {
        return eprintln!("GBAV_CDIFF not set; skipped");
    };
    let Some(roms) = gbav_testkit::playyan_roms() else { return eprintln!("PY_ROMS not set; skipped") };
    let fw = Firmware::from_paths(&[roms.join("Play-Yan Micro (Japan).gba")]).unwrap();
    let img = fw.image(ImageId::MiFw0).unwrap();
    let mut files = Vec::new();
    if let Some(m) = env_dir("PY_MEDIA") {
        files.push(m.join("play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4"));
    }
    if let Some(c) = env_dir("PY_AAC_CORPUS") {
        for e in std::fs::read_dir(c).unwrap().flatten() {
            if e.path().extension().is_some_and(|x| x == "m4a" || x == "mp4") {
                files.push(e.path());
            }
        }
    }
    files.sort();
    if files.is_empty() {
        return eprintln!("no AAC input (PY_MEDIA / PY_AAC_CORPUS); skipped");
    }
    let copen: OpenFn = csym!(lib, "pyaac_open");
    let cinit: InitFn = csym!(lib, "pyaac_init");
    let cframe: FrameFn = csym!(lib, "pyaac_frame");
    let cerr: ErrFn = csym!(lib, "pyaac_error");
    let cclose: CloseFn = csym!(lib, "pyaac_close");
    let iters = gbav_testkit::fuzz_iters();
    let mut rng = Rng::new(0x6161_6300);
    let (mut frames, mut outside) = (0usize, 0usize);
    for f in &files {
        let data = std::fs::read(f).unwrap();
        let mp4 = Mp4::parse(&data).unwrap();
        let Some(tr) = mp4.audio() else { continue };
        let asc = tr.config();
        let mut c = copen(img.bytes.as_ptr(), img.bytes.len(), 0);
        assert_eq!(cinit(c, asc.as_ptr(), asc.len()), 0);
        let mut r = Decoder::new(img, asc).unwrap();
        let mut cpcm = vec![0i16; 2048];
        let n = tr.samples.len().min(iters.max(1) * 4);
        let mut history: Vec<Vec<u8>> = Vec::new();
        for i in 0..n {
            let raw = tr.sample_data(&data, i).unwrap();
            let raw = if rng.chance(0.25) { corrupt(&mut rng, raw, 0) } else { raw.to_vec() };
            history.push(raw.clone());
            if let Some(dump) = std::env::var_os("AAC_CDIFF_DUMP") {
                // frames since the last restart, length-prefixed, for tools/playyan/aac (C vs firmware)
                let mut v = Vec::new();
                for h in &history {
                    v.extend_from_slice(&(h.len() as u32).to_le_bytes());
                    v.extend_from_slice(h);
                }
                std::fs::write(&dump, v).unwrap();
            }
            let cr = cframe(c, raw.as_ptr(), raw.len(), cpcm.as_mut_ptr());
            let rr = r.decode_frame_raw(&raw);
            let want = match cr {
                -1 => Some(Err(cerr(c))),
                x if x < -1 => None,
                x => Some(Ok(x as u32)),
            };
            assert_eq!(rr, want, "{} frame {i}", f.display());
            match rr {
                Some(Ok(_)) => assert_eq!(r.pcm_buffer(), &cpcm[..], "{} frame {i}: PCM", f.display()),
                None => {
                    // outside the model: restart both from a clean state (fresh PNS random state too)
                    outside += 1;
                    cclose(c);
                    c = copen(img.bytes.as_ptr(), img.bytes.len(), 0);
                    cinit(c, asc.as_ptr(), asc.len());
                    r = Decoder::new(img, asc).unwrap();
                    history.clear();
                }
                _ => {}
            }
            frames += 1;
        }
        cclose(c);
    }
    eprintln!("aac cdiff: {frames} frames, {outside} outside the model");
}
