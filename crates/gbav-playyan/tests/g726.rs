//! G.726 against the firmware oracle (tools/playyan/g726/oracle.py output; PY_G726_ORACLE=file):
//! the video firmware's whole payload decoder 0x1234e run in the VC01 emulator.

use gbav_playyan::g726::Decoder;

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len() / 2).map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap()).collect()
}

#[test]
fn g726_vs_firmware() {
    let Some(f) = std::env::var_os("PY_G726_ORACLE") else {
        eprintln!("PY_G726_ORACLE not set; skipped");
        return;
    };
    let text = std::fs::read_to_string(f).unwrap();
    let mut dec: Option<Decoder> = None;
    let (mut ok, mut n, mut samples) = (0, 0, 0);
    for line in text.lines() {
        let p: Vec<&str> = line.split_whitespace().collect();
        let bits: u32 = p[0].parse().unwrap();
        if bits == 0 {
            dec = None;
            continue;
        }
        let d = dec.get_or_insert_with(|| Decoder::new(bits).unwrap());
        let mut out = Vec::new();
        d.decode_payload(&unhex(p[1]), &mut out);
        let want: Vec<i16> = unhex(p[2]).as_chunks::<2>().0.iter().map(|c| i16::from_le_bytes([c[0], c[1]])).collect();
        n += 1;
        samples += want.len();
        if out == want {
            ok += 1;
        } else {
            eprintln!("bits {bits}: payload {} differs", p[1].len() / 2);
        }
    }
    eprintln!("G.726: {ok}/{n} payloads ({samples} samples) bit-exact vs the firmware");
    assert_eq!(ok, n);
}

/// Rust vs the C reference (reference/playyan/g726) on random code streams at every rate,
/// including saturating bursts (GBAV_CDIFF=1, GBAV_CREF=reference).
#[test]
fn g726_rust_vs_c() {
    use gbav_testkit::{CLib, Rng};
    use std::ffi::c_void;
    let Some(lib) = CLib::build("pyg726", &["playyan/g726/g726.c"], &[], std::path::Path::new(env!("CARGO_TARGET_TMPDIR"))) else {
        eprintln!("GBAV_CDIFF not set; skipped");
        return;
    };
    type Init = unsafe extern "C" fn(*mut c_void, i32);
    type Dec = unsafe extern "C" fn(*mut c_void, u32) -> i16;
    // SAFETY: prototypes of g726.h; the state struct is 31 ints, over-allocated here.
    let (init, dec) = unsafe {
        (std::mem::transmute::<*mut c_void, Init>(lib.sym("pyg726_init")), std::mem::transmute::<*mut c_void, Dec>(lib.sym("pyg726_decode")))
    };
    let mut rng = Rng::new(726);
    let mut n = 0u64;
    for bits in 2..=5u32 {
        for _ in 0..gbav_testkit::fuzz_iters() / 4 + 1 {
            let mut st = vec![0i32; 64];
            unsafe { init(st.as_mut_ptr() as *mut c_void, bits as i32) };
            let mut r = Decoder::new(bits).unwrap();
            let burst = rng.below(1 << bits) as u32;
            for k in 0..2000 {
                let c = if (k / 64) % 3 == 1 { burst } else { rng.below(1 << bits) as u32 };
                let a = r.decode_code(c);
                let b = unsafe { dec(st.as_mut_ptr() as *mut c_void, c) };
                assert_eq!(a, b, "bits {bits} sample {k}");
                n += 1;
            }
        }
    }
    eprintln!("G.726: {n} samples identical in Rust and C");
}
