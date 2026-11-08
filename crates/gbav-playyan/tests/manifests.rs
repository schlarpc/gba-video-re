//! Every manifest set in PY_VERIFY (default verify/playyan; `<image>_<stem>.*_md5.csv.gz`, made from the real firmware in
//! the full-system emulator) against our decode of the media file with that stem, found under
//! PY_MEDIA or the colon-separated PY_VERIFY_MEDIA directories (searched recursively).
//! Firmware from PY_ROMS (Play-Yan ROM, Micro ROM, playan.fup). Skipped without the variables.

use gbav_playyan::{verify, Firmware, ImageId};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

fn walk(d: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(d) else { return };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            walk(&p, out);
        } else {
            out.push(p);
        }
    }
}

#[test]
fn manifests() {
    let (Some(vd), Some(roms)) = (gbav_testkit::playyan_verify(), gbav_testkit::playyan_roms()) else {
        eprintln!("PY_ROMS (or GBAV_ROMS) not set or verify/playyan missing; skipped");
        return;
    };
    let files: Vec<PathBuf> = ["Play-Yan (Japan).gba", "Play-Yan Micro (Japan).gba", "playan.fup"].iter().map(|f| roms.join(f)).filter(|p| p.is_file()).collect();
    let fw = Firmware::from_paths(&files).unwrap();
    let mut media = Vec::new();
    for v in ["PY_MEDIA", "PY_VERIFY_MEDIA"] {
        if let Some(s) = std::env::var_os(v) {
            for d in std::env::split_paths(&s) {
                walk(&d, &mut media);
            }
        }
    }
    let mut stems = std::collections::BTreeMap::new();
    for p in &media {
        let ext = p.extension().map(|e| e.to_string_lossy().to_ascii_lowercase()).unwrap_or_default();
        if matches!(ext.as_str(), "asf" | "mp3" | "mp4") {
            if let Ok(b) = std::fs::read(p) {
                stems.entry(gbav_playyan::verify::manifest_stem(&vd, p, &b)).or_insert_with(|| p.clone());
            }
        }
    }
    let prefixes: BTreeSet<String> = std::fs::read_dir(&vd)
        .unwrap()
        .flatten()
        .filter_map(|e| e.file_name().to_str().and_then(|n| n.strip_suffix(".csv.gz")).and_then(|n| n.split_once('.')).map(|(p, _)| p.to_string()))
        .collect();
    let (mut ok, mut n) = (0, 0);
    for pre in prefixes {
        let Some(id) = ImageId::ALL.into_iter().find(|i| pre.starts_with(&format!("{}_", i.name()))) else { continue };
        let stem = &pre[id.name().len() + 1..];
        let file = stems.get(stem);
        let Some(file) = file else {
            eprintln!("{pre}: media file not found; skipped");
            continue;
        };
        let Some(img) = fw.image(id) else {
            eprintln!("{pre}: firmware image not available; skipped");
            continue;
        };
        let b = std::fs::read(file).unwrap();
        let (video, music) = if id.is_video() { (Some(img), fw.music()) } else { (fw.video(), Some(img)) };
        match verify::verify(video, music, &b, &vd, &pre, 28) {
            Ok(r) => {
                n += 1;
                ok += r.passed() as usize;
                eprintln!("{} {pre}: {:?}", if r.passed() { "OK  " } else { "FAIL" }, r);
            }
            Err(e) => {
                n += 1;
                eprintln!("ERR  {pre}: {e}");
            }
        }
    }
    eprintln!("manifests: {ok}/{n} sets pass");
    assert_eq!(ok, n);
}
