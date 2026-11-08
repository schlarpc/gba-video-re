//! Differential tests: the Rust MPEG-4 decoder against the C reference (reference/playyan/video,
//! built after `make -C reference/playyan/video FW=...` has generated fw_tables.h), on Nintendo's
//! files and on corrupted copies (GBAV_CDIFF=1, GBAV_CREF=reference, PY_ROMS, PY_MEDIA;
//! GBAV_FUZZ_ITERS per file, default 300).

use gbav_playyan::{asf::Asf, m4v, pixel, Firmware, ImageId};
use gbav_testkit::{corrupt, fuzz_iters, CLib, Rng};
use std::ffi::c_void;
use std::path::PathBuf;

type New = unsafe extern "C" fn() -> *mut c_void;
type Free = unsafe extern "C" fn(*mut c_void);
type Decode = unsafe extern "C" fn(*mut c_void, *const u8, usize) -> i32;
type FrameFn = unsafe extern "C" fn(*const c_void, u32, *mut u16) -> i32;
type Reset = unsafe extern "C" fn();

struct C {
    _lib: CLib,
    new: New,
    free: Free,
    decode: Decode,
    frame: FrameFn,
    reset: Reset,
}

fn clib() -> Option<C> {
    let base = gbav_testkit::cref_dir()?;
    if !base.join("playyan/video/fw_tables.h").is_file() {
        eprintln!("reference/playyan/video/fw_tables.h missing (run make there with FW=...); skipped");
        return None;
    }
    let lib = CLib::build("pyvideo", &["playyan/video/asf.c", "playyan/video/m4v.c", "playyan/video/pixel.c"], &[], std::path::Path::new(env!("CARGO_TARGET_TMPDIR")))?;
    // SAFETY: symbols of the library just built, with the C prototypes of pyvideo.h.
    unsafe {
        Some(C {
            new: std::mem::transmute::<*mut c_void, New>(lib.sym("pyv_dec_new")),
            free: std::mem::transmute::<*mut c_void, Free>(lib.sym("pyv_dec_free")),
            decode: std::mem::transmute::<*mut c_void, Decode>(lib.sym("pyv_dec_decode")),
            frame: std::mem::transmute::<*mut c_void, FrameFn>(lib.sym("pyv_frame_rgb565")),
            reset: std::mem::transmute::<*mut c_void, Reset>(lib.sym("pyv_fit_reset")),
            _lib: lib,
        })
    }
}

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

/// Decode `objs` with both; returns the number of frames compared. Panics on a difference.
fn run_both(c: &C, img: &gbav_playyan::Image, config: &[u8], objs: &[Vec<u8>], what: &str) -> usize {
    let mut r = m4v::Decoder::new(img, config).unwrap();
    let mut fit = pixel::Fitter::new(&pixel::ThumbTables::from_firmware(img).unwrap());
    // SAFETY: the C decoder is used single-threaded through its documented API.
    let d = unsafe { (c.reset)(); (c.new)() };
    if !config.is_empty() {
        unsafe { (c.decode)(d, config.as_ptr(), config.len()) };
    }
    let mut n = 0;
    let mut cf = vec![0u16; 240 * 160];
    for (i, o) in objs.iter().enumerate() {
        let rr = r.decode(o);
        let cr = unsafe { (c.decode)(d, o.as_ptr(), o.len()) };
        match rr {
            Err(_) => {
                assert!(cr < 0, "{what} object {i}: Rust error, C {cr}");
                break;
            }
            Ok(k) => {
                let want = match k {
                    m4v::Decoded::Headers => 0,
                    m4v::Decoded::Frame(m4v::VopType::I) => 1,
                    m4v::Decoded::Frame(m4v::VopType::P) => 2,
                    m4v::Decoded::NotCoded => 3,
                };
                assert_eq!(cr, want, "{what} object {i}: result");
                if want == 1 || want == 2 {
                    let rf = pixel::frame_rgb565(&mut fit, r.picture(), 0x140).unwrap();
                    assert_eq!(unsafe { (c.frame)(d, 0x140, cf.as_mut_ptr()) }, 0);
                    assert!(rf == cf, "{what} object {i}: frames differ");
                    n += 1;
                }
            }
        }
    }
    unsafe { (c.free)(d) };
    n
}

#[test]
fn video_rust_vs_c() {
    let (Some(c), Some(roms), Some(media)) = (clib(), gbav_testkit::playyan_roms(), env_dir("PY_MEDIA")) else {
        eprintln!("GBAV_CDIFF / PY_ROMS / PY_MEDIA not set; skipped");
        return;
    };
    // the C library is built for one image (make FW=...); GBAV_CDIFF_IMAGE names it (default py_fw0)
    let files: Vec<PathBuf> = ["Play-Yan (Japan).gba", "Play-Yan Micro (Japan).gba", "playan.fup"].iter().map(|f| roms.join(f)).filter(|p| p.is_file()).collect();
    let fw = Firmware::from_paths(&files).unwrap();
    let want = std::env::var("GBAV_CDIFF_IMAGE").unwrap_or_else(|_| "py_fw0".into());
    let id = ImageId::ALL.into_iter().find(|i| i.name() == want).expect("GBAV_CDIFF_IMAGE: unknown image");
    let img = fw.image(id).unwrap();
    let mut files: Vec<PathBuf> = vec![media.join("playan_support_sample_movie/sample_movie.asf"), media.join("playan_garage_fish/FISH.ASF")];
    if let Some(d) = env_dir("PY_VTESTS") {
        let mut v: Vec<PathBuf> = std::fs::read_dir(d).unwrap().filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.extension().is_some_and(|x| x == "asf")).collect();
        v.sort();
        files.extend(v);
    }
    let mut rng = Rng::new(0x9fa);
    let (mut frames, mut fuzzed) = (0, 0);
    for f in files {
        let b = std::fs::read(&f).unwrap();
        let asf = Asf::parse(&b).unwrap();
        let config = asf.video.as_ref().unwrap().codec_data.clone();
        let objs: Vec<Vec<u8>> = asf.video_objects.iter().map(|o| o.data.clone()).collect();
        let name = f.file_name().unwrap().to_string_lossy().to_string();
        frames += run_both(&c, img, &config, &objs, &name);
        // fuzz: corrupt one object of a short window starting at an I-frame
        let keys: Vec<usize> = (0..objs.len()).filter(|&i| asf.video_objects[i].key).collect();
        for _ in 0..fuzz_iters() / 10 {
            let k = keys[rng.below(keys.len() as u64) as usize];
            let mut win: Vec<Vec<u8>> = objs[k..(k + 12).min(objs.len())].to_vec();
            let j = rng.below(win.len() as u64) as usize;
            win[j] = corrupt(&mut rng, &win[j], 4);
            run_both(&c, img, &config, &win, &format!("{name} fuzz"));
            fuzzed += 1;
        }
    }
    eprintln!("video: {frames} frames identical in Rust and C; {fuzzed} corrupted windows identical");
}
