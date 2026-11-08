//! Firmware extraction from the user's files (`PY_ROMS`: directory with the Play-Yan ROMs and
//! playan.fup; skipped without it).

use gbav_playyan::{Firmware, ImageId};

fn roms() -> Option<std::path::PathBuf> {
    gbav_testkit::playyan_roms()
}

#[test]
fn extract_all_images() {
    let Some(d) = roms() else {
        eprintln!("PY_ROMS not set; skipped");
        return;
    };
    let cases: [(&str, &[ImageId]); 3] = [
        ("Play-Yan (Japan).gba", &[ImageId::PyFw0, ImageId::PyFw1]),
        ("Play-Yan Micro (Japan).gba", &[ImageId::MiFw0, ImageId::MiFw1]),
        ("playan.fup", &[ImageId::FupSec0, ImageId::FupSec1]),
    ];
    for (f, ids) in cases {
        let p = d.join(f);
        if !p.is_file() {
            eprintln!("{f} missing; skipped");
            continue;
        }
        let fw = Firmware::from_paths(&[p]).unwrap();
        for id in ids {
            assert!(fw.image(*id).is_some(), "{f}: {id:?} not found");
        }
    }
}
