//! Differential fuzzing of the Rust MP3 decoder against the C reference
//! (`reference/playyan/mp3`), with `GBAV_CDIFF=1` (and `GBAV_CREF` pointing at the `reference/`
//! directory that holds `playyan/`). Frames of the sample and corpus files get bit flips in the
//! side info and main data and intensity-stereo mode extensions; both decoders run with a zeroed
//! heap (`strict = false`) and must agree on every sample. The C reference itself is fuzzed
//! against the firmware in the emulator by `tools/playyan/mp3/fuzz.py`.

use gbav_playyan::mp3::{DecodeResult, Decoder};
use gbav_playyan::mp3file::Mp3File;
use gbav_playyan::{Firmware, ImageId};
use gbav_testkit::{csym, CLib, Rng};
use std::ffi::c_void;
use std::path::PathBuf;

type NewFn = extern "C" fn(*const u8, usize, i32) -> *mut c_void;
type DecFn = extern "C" fn(*mut c_void, *const u8, usize, *mut i16, *mut i32) -> i32;
type StrictFn = extern "C" fn(*mut c_void, i32);
type FreeFn = extern "C" fn(*mut c_void);

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

#[test]
fn mp3_fuzz_vs_c() {
    let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    let Some(lib) = CLib::build("pymp3", &["playyan/mp3/pymp3.c", "playyan/mp3/pymp3file.c"], &[], &out) else {
        return eprintln!("GBAV_CDIFF not set; skipped");
    };
    let Some(roms) = gbav_testkit::playyan_roms() else { return eprintln!("PY_ROMS not set; skipped") };
    let fw = Firmware::from_paths(&[roms.join("Play-Yan (Japan).gba")]).unwrap();
    let img = fw.image(ImageId::PyFw1).unwrap();
    let mut files = Vec::new();
    if let Some(m) = env_dir("PY_MEDIA") {
        files.push(m.join("playan_support_sample_music/sample_music.mp3"));
    }
    if let Some(c) = env_dir("PY_MP3_CORPUS") {
        for e in std::fs::read_dir(c).unwrap().flatten() {
            if e.path().extension().is_some_and(|x| x == "mp3") {
                files.push(e.path());
            }
        }
    }
    if files.is_empty() {
        return eprintln!("no MP3 input (PY_MEDIA / PY_MP3_CORPUS); skipped");
    }
    let cnew: NewFn = csym!(lib, "pymp3_new");
    let cdec: DecFn = csym!(lib, "pymp3_decode_frame");
    let cstrict: StrictFn = csym!(lib, "pymp3_set_strict");
    let cfree: FreeFn = csym!(lib, "pymp3_free");
    let iters = gbav_testkit::fuzz_iters();
    let mut rng = Rng::new(0x6d70_3300);
    let (mut frames, mut compared) = (0, 0);
    for it in 0..iters {
        let path = &files[rng.below(files.len() as u64) as usize];
        let data = std::fs::read(path).unwrap();
        let mf = Mp3File::parse(&data).unwrap();
        let list: Vec<_> = mf.frames().collect();
        let k0 = rng.below(list.len().saturating_sub(10).max(1) as u64) as usize;
        let mut rd = Decoder::new(img).unwrap();
        rd.strict = false;
        let c = cnew(img.bytes.as_ptr(), img.bytes.len(), 0);
        assert!(!c.is_null());
        cstrict(c, 0);
        let mut pcm = vec![0i16; 2304];
        for f in list.iter().skip(k0).take(10) {
            let mut b = data[f.offset..f.offset + f.avail].to_vec();
            b.resize(1449, 0);
            if rng.chance(0.5) {
                let crc = if b[1] & 1 != 0 { 4 } else { 6 };
                match rng.below(3) {
                    0 => b[3] = (b[3] & !0xf0) | 0x40 | ((rng.pick(&[1u8, 3])) << 4),
                    1 => {
                        for _ in 0..rng.range(1, 4) {
                            let p = crc + rng.below(32) as usize;
                            b[p] ^= 1 << rng.below(8);
                        }
                    }
                    _ => {
                        for _ in 0..rng.range(1, 9) {
                            let p = crc + 32 + rng.below((f.len.max(40) - 36) as u64) as usize;
                            b[p.min(1448)] ^= 1 << rng.below(8);
                        }
                    }
                }
            }
            frames += 1;
            let (rr, _) = rd.decode_frame_status(&b);
            let mut used = 0;
            let rc = cdec(c, b.as_ptr(), b.len(), pcm.as_mut_ptr(), &mut used);
            let rcode = match rr {
                DecodeResult::Ok => 1,
                DecodeResult::Silence => 2,
                DecodeResult::BadHeader => 0,
                DecodeResult::Undefined => -1,
                DecodeResult::Unsupported => -2,
            };
            assert_eq!(rcode, rc, "iteration {it}: result codes differ at frame {:#x} of {}", f.offset, path.display());
            if rc < 0 {
                break;
            }
            if rc > 0 {
                compared += 1;
                assert_eq!(rd.pcm(), &pcm[..], "iteration {it}: PCM differs at frame {:#x} of {}", f.offset, path.display());
            }
        }
        cfree(c);
    }
    eprintln!("mp3 C diff: {frames} frames, {compared} compared, all equal");
}
