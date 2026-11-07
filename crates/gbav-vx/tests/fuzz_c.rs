//! Differential test against the reference C decoder: replays a corpus written by
//! tests/fuzz/gen_corpus.py (random valid VX++ / VXGB frames, plus garbage tails, decoded by
//! libvxdec.so) and compares the whole EWRAM image, the final bit position and the error
//! count after every case.
//!
//!   GBAV_ROMS=... GBAV_VX_FUZZ=corpus.bin[,corpus2.bin] cargo test --release -p gbav-vx --test fuzz_c
//!
//! With `GBAV_CDIFF=1` and no `GBAV_VX_FUZZ`, the test builds the C reference
//! (`make -C reference/vx/decoder` with the Shrek Rev 6 / Rev 5 ROMs) and generates a corpus of
//! `GBAV_FUZZ_ITERS` cases (default 300) per variant itself; that needs python3 with the
//! `unicorn` and `capstone` modules (the generators import the oracle helpers).

mod common;

use gbav_common::md5::{digest, to_hex as hex};
use gbav_vx::{Core, Tables, Variant};

/// Build libvxdec.so from reference/vx/decoder and write a fresh corpus (see the module docs).
fn generate_corpus() -> Option<std::path::PathBuf> {
    use std::process::Command;
    gbav_testkit::cref_dir()?;
    let tmp = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    // make can not take paths with spaces (No-Intro names): link the ROMs under short names
    let link = |id: &str| -> Option<std::path::PathBuf> {
        let l = tmp.join(format!("{id}.gba"));
        let _ = std::fs::remove_file(&l);
        std::os::unix::fs::symlink(gbav_testkit::rom_path(id)?, &l).ok()?;
        Some(l)
    };
    let (pp, gb) = (link("shrek-rev6")?, link("shrek-rev5")?);
    let repo = gbav_testkit::repo_root();
    let dec = repo.join("reference/vx/decoder");
    let st = Command::new("make")
        .arg("-s")
        .arg("-C")
        .arg(&dec)
        .arg(format!("ROM={}", pp.display()))
        .arg(format!("VXGB_ROM={}", gb.display()))
        .arg("libvxdec.so")
        .status()
        .expect("run make");
    assert!(st.success(), "make -C reference/vx/decoder failed");
    let out = tmp.join("vx_fuzz.bin");
    let st = Command::new("python3")
        .arg(repo.join("crates/gbav-vx/tests/fuzz/gen_corpus.py"))
        .arg("--lib")
        .arg(dec.join("libvxdec.so"))
        .arg("--vxpp-rom")
        .arg(&pp)
        .arg("--vxgb-rom")
        .arg(&gb)
        .args(["--cases", &gbav_testkit::fuzz_iters().to_string(), "--seed", "1", "--out"])
        .arg(&out)
        .status()
        .expect("run python3");
    assert!(st.success(), "gen_corpus.py failed (needs python3 with unicorn and capstone)");
    Some(out)
}

fn rd32(b: &[u8], p: &mut usize) -> u32 {
    let v = u32::from_le_bytes(b[*p..*p + 4].try_into().unwrap());
    *p += 4;
    v
}

#[test]
fn fuzz_against_c() {
    let corpora = match std::env::var_os("GBAV_VX_FUZZ") {
        Some(c) => c,
        None => match generate_corpus() {
            Some(c) => c.into_os_string(),
            None => {
                eprintln!("GBAV_VX_FUZZ not set and GBAV_CDIFF not enabled (or no ROMs): skipping");
                return;
            }
        },
    };
    let (Some(pp), Some(gb)) = (
        common::load_rom(&common::cart("shrek-rev6")),
        common::load_rom(&common::cart("shrek-rev5")),
    ) else {
        return;
    };
    let tabs = [
        Tables::from_rom(&pp, Variant::VxPP).unwrap(),
        Tables::from_rom(&gb, Variant::Vxgb).unwrap(),
    ];
    let (mut total, mut fails, mut with_err) = (0usize, Vec::new(), 0usize);
    for path in corpora.to_str().unwrap().split(',') {
        let b = std::fs::read(path).unwrap();
        assert_eq!(&b[..4], b"VXFZ");
        let mut p = 4;
        let n = rd32(&b, &mut p);
        for case in 0..n {
            let (vxgb, nframes) = (b[p] as usize, b[p + 1] as usize);
            p += 2;
            let qp = rd32(&b, &mut p);
            let slen = rd32(&b, &mut p) as usize;
            let stream = &b[p..p + slen];
            p += slen;
            let mut core = Core::new(tabs[vxgb].clone(), 240, 160, qp, stream);
            for _ in 0..rd32(&b, &mut p) {
                let off = rd32(&b, &mut p) as usize;
                let len = rd32(&b, &mut p) as usize;
                core.ew[off..off + len].copy_from_slice(&b[p..p + len]);
                p += len;
            }
            let want_md5 = &b[p..p + 16];
            p += 16;
            let want_bit = rd32(&b, &mut p);
            let want_err = rd32(&b, &mut p);
            for _ in 0..nframes {
                core.decode_frame(stream);
            }
            let got_md5 = digest(&core.ew[..]);
            let (got_bit, got_err) = (core.bits.bitpos(), core.stats.err as u32);
            total += 1;
            with_err += (want_err > 0) as usize;
            if got_md5 != want_md5 || got_bit != want_bit || got_err != want_err {
                if let Some(d) = std::env::var_os("GBAV_VX_FUZZ_DUMP") {
                    let name = std::path::Path::new(path).file_stem().unwrap().to_string_lossy().to_string();
                    std::fs::write(std::path::Path::new(&d).join(format!("{name}_{case}.rust.ewram")), &core.ew[..]).unwrap();
                }
                fails.push(format!(
                    "{path} case {case} ({}, {nframes} frames): ewram {} bits {got_bit}/{want_bit} err {got_err}/{want_err}",
                    if vxgb == 1 { "VXGB" } else { "VX++" },
                    if got_md5 == want_md5 { "same".to_string() } else { format!("DIFF {} vs {}", hex(&got_md5), hex(want_md5.try_into().unwrap())) }
                ));
            }
        }
        assert_eq!(p, b.len());
    }
    println!(
        "fuzz vs C: {} cases ({} raise decoder error flags), {} mismatches",
        total,
        with_err,
        fails.len()
    );
    for f in fails.iter().take(10) {
        println!("  {f}");
    }
    assert!(fails.is_empty());
}
