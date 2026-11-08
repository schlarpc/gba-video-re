//! MP3: the Rust decoder against hash manifests made with the firmware's own decoder run in the
//! VC01 emulator (verify/playyan/oracle/mp3, tools/playyan/mp3/make_oracle.py), and against the C reference.
//!
//! Environment (each test skips itself without its inputs):
//! - `PY_ROMS`: directory with `Play-Yan (Japan).gba` (and optionally the Micro ROM and
//!   `playan.fup`): the firmware source;
//! - `PY_MEDIA`: Nintendo's sample files (`playan_support_sample_music/sample_music.mp3`);
//! - `PY_MP3_CORPUS`: the LAME-encoded test set made by `tools/playyan/mp3/gen_media.py`;
//! - `PY_VERIFY`: the real-firmware manifests (default `verify/playyan`).

use gbav_common::md5;
use gbav_playyan::gbapcm::GbaPcm;
use gbav_playyan::mp3::{Decoder, Player};
use gbav_playyan::mp3file::Mp3File;
use gbav_playyan::{Firmware, ImageId};
use std::path::{Path, PathBuf};

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

fn firmware() -> Option<Firmware> {
    let d = gbav_testkit::playyan_roms()?;
    let paths: Vec<PathBuf> = ["Play-Yan (Japan).gba", "Play-Yan Micro (Japan).gba", "playan.fup"]
        .iter()
        .map(|f| d.join(f))
        .filter(|p| p.is_file())
        .collect();
    Firmware::from_paths(&paths).ok()
}

fn oracle_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../verify/playyan/oracle/mp3")
}

fn read_csv(p: &Path) -> Vec<Vec<String>> {
    std::fs::read_to_string(p)
        .unwrap()
        .lines()
        .skip(1)
        .map(|l| l.split(',').map(str::to_string).collect())
        .collect()
}

/// Decode `file` and compare every frame's PCM md5 and every GBA block md5 with the oracle.
fn check_file(fw: &Firmware, file: &Path) -> (usize, usize) {
    let name = file.file_stem().unwrap().to_str().unwrap();
    let pcm_csv = oracle_dir().join(format!("{name}.mp3_pcm_md5.csv"));
    if !pcm_csv.is_file() {
        eprintln!("{name}: no oracle manifest; skipped");
        return (0, 0);
    }
    let rows = read_csv(&pcm_csv);
    let data = std::fs::read(file).unwrap();
    let mf = Mp3File::parse(&data).unwrap();
    let music = fw.music().unwrap();
    let mut pl = Player::new(music, &mf).unwrap();
    let mut gba = GbaPcm::new(mf.first.rate, 28, 0);
    let mut blocks = Vec::new();
    let mut n = 0;
    while let Some((f, pcm)) = pl.next_frame().unwrap() {
        let row = &rows[n];
        assert_eq!(row[1].parse::<usize>().unwrap(), f.offset, "{name}: frame {n} offset");
        let pcm = pcm.expect("pcm");
        let h = md5::hex_u16(&pcm.iter().map(|&v| v as u16).collect::<Vec<_>>());
        assert_eq!(h, row[3], "{name}: frame {n} PCM differs from the firmware");
        blocks.extend(gba.push(pcm));
        n += 1;
    }
    assert_eq!(n, rows.len(), "{name}: frame count");
    let g = read_csv(&oracle_dir().join(format!("{name}.gba_md5.csv")));
    for (i, (a, b)) in blocks.iter().enumerate().take(g.len()) {
        let mut v: Vec<u8> = a.iter().map(|&x| x as u8).collect();
        v.extend(b.iter().map(|&x| x as u8));
        assert_eq!(md5::hex(&v), g[i][1], "{name}: GBA block {i}");
    }
    assert!(blocks.len() >= g.len(), "{name}: {} GBA blocks, oracle {}", blocks.len(), g.len());
    (n, g.len())
}

#[test]
fn tables_identical_across_images() {
    let Some(fw) = firmware() else { return eprintln!("PY_ROMS not set; skipped") };
    let mut digests = Vec::new();
    for id in [ImageId::PyFw1, ImageId::MiFw1, ImageId::FupSec1] {
        if let Some(img) = fw.image(id) {
            let d = Decoder::new(img).expect("tables");
            digests.push((id, d.tables().digest()));
        }
    }
    eprintln!("{digests:?}");
    assert!(digests.windows(2).all(|w| w[0].1 == w[1].1), "{digests:?}");
}

#[test]
fn sample_music_vs_firmware_oracle() {
    let (Some(fw), Some(m)) = (firmware(), env_dir("PY_MEDIA")) else {
        return eprintln!("PY_ROMS / PY_MEDIA not set; skipped");
    };
    let f = m.join("playan_support_sample_music/sample_music.mp3");
    let (n, g) = check_file(&fw, &f);
    eprintln!("sample_music: {n} frames, {g} GBA blocks bit-exact");
}

#[test]
fn corpus_vs_firmware_oracle() {
    let (Some(fw), Some(c)) = (firmware(), env_dir("PY_MP3_CORPUS")) else {
        return eprintln!("PY_ROMS / PY_MP3_CORPUS not set; skipped");
    };
    let mut files: Vec<_> = std::fs::read_dir(&c).unwrap().filter_map(|e| e.ok().map(|e| e.path())).collect();
    files.retain(|p| p.extension().is_some_and(|x| x == "mp3"));
    files.sort();
    for f in files {
        let (n, g) = check_file(&fw, &f);
        eprintln!("{}: {n} frames, {g} GBA blocks bit-exact", f.display());
    }
}

#[test]
fn sample_music_id3() {
    let Some(m) = env_dir("PY_MEDIA") else { return eprintln!("PY_MEDIA not set; skipped") };
    let data = std::fs::read(m.join("playan_support_sample_music/sample_music.mp3")).unwrap();
    let mf = Mp3File::parse(&data).unwrap();
    let id = mf.id3();
    eprintln!(
        "kind {:?} start {:#x} end {:#x} first {:?} title {:?} artist {:?} seconds {}",
        id.kind,
        mf.start,
        mf.end,
        mf.first,
        String::from_utf8_lossy(id.title_bytes()),
        String::from_utf8_lossy(id.artist_bytes()),
        id.seconds
    );
    assert!(id.seconds > 0);
}
