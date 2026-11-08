//! Video end-to-end checks. Gates: PY_ROMS (directory with the Play-Yan ROMs / playan.fup),
//! PY_MEDIA (Nintendo's sample media, laid out as in docs/playyan/decoders.md), PY_FRAMES
//! (optional: raw RGB565 240x160 frame dumps of the real firmware, f%05d.bin).

use gbav_playyan::{asf::Asf, m4v, pixel, Firmware, ImageId};
use std::path::PathBuf;

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

fn firmware() -> Option<Firmware> {
    let d = gbav_testkit::playyan_roms()?;
    let files: Vec<PathBuf> = ["Play-Yan (Japan).gba", "Play-Yan Micro (Japan).gba", "playan.fup"]
        .iter()
        .map(|f| d.join(f))
        .filter(|p| p.is_file())
        .collect();
    Firmware::from_paths(&files).ok()
}

/// protocol.md 9: the GBA ROM's Garage Game table (name, hash).
const GARAGE: [(&str, &str, u32); 13] = [
    ("insect", "INSECT.ASF", 0x4D5E5AEB),
    ("avoid", "AVOID.ASF", 0xF8CB9008),
    ("bat", "BAT.ASF", 0xD911FE57),
    ("credit", "CREDIT.ASF", 0x02F86041),
    ("jump", "JUMP.ASF", 0x748C5BAF),
    ("fire", "FIRE.ASF", 0x0D78A9FE),
    ("keeper", "KEEPER.ASF", 0x3FEBEFE5),
    ("memory", "MEMORY.ASF", 0x90119AE6),
    ("nekoroid", "NEKOROID.ASF", 0xD839D809),
    ("nose", "NOSE.ASF", 0x86D3BA78),
    ("fish", "FISH.ASF", 0x5741DE47),
    ("triple", "TRIPLE.ASF", 0xD5C0A40B),
    ("wave", "WAVE.ASF", 0xE7DCD82B),
];

#[test]
fn garage_thumbnail_hashes() {
    let (Some(fw), Some(media)) = (firmware(), env_dir("PY_MEDIA")) else {
        eprintln!("PY_ROMS / PY_MEDIA not set; skipped");
        return;
    };
    for id in [ImageId::PyFw0, ImageId::MiFw0, ImageId::FupSec0] {
        let Some(img) = fw.image(id) else { continue };
        let tt = pixel::ThumbTables::from_firmware(img).unwrap();
        let mut ok = 0;
        for (dir, name, hash) in GARAGE {
            let b = std::fs::read(media.join(format!("playan_garage_{dir}")).join(name)).unwrap();
            let asf = Asf::parse(&b).unwrap();
            let mut dec = m4v::Decoder::new(img, &asf.video.as_ref().unwrap().codec_data).unwrap();
            let mut got = None;
            for o in &asf.video_objects {
                if let m4v::Decoded::Frame(_) = dec.decode(&o.data).unwrap() {
                    got = Some(pixel::thumbnail_bgr555(dec.picture(), &tt).unwrap());
                    break;
                }
            }
            let h = pixel::garage_hash(&got.unwrap());
            if h == hash {
                ok += 1;
            } else {
                eprintln!("{}: {name} hash {h:08x}, table {hash:08x}", id.name());
            }
        }
        eprintln!("{}: Garage thumbnail hashes {ok}/13", id.name());
        assert_eq!(ok, 13);
    }
}

#[test]
fn sample_movie_vs_firmware_frames() {
    let (Some(fw), Some(media), Some(frames)) = (firmware(), env_dir("PY_MEDIA"), env_dir("PY_FRAMES")) else {
        eprintln!("PY_ROMS / PY_MEDIA / PY_FRAMES not set; skipped");
        return;
    };
    let img = fw.image(ImageId::PyFw0).unwrap();
    let b = std::fs::read(media.join("playan_support_sample_movie/sample_movie.asf")).unwrap();
    let asf = Asf::parse(&b).unwrap();
    let mut dec = m4v::Decoder::new(img, &asf.video.as_ref().unwrap().codec_data).unwrap();
    let mut fit = pixel::Fitter::new(&pixel::ThumbTables::from_firmware(img).unwrap());
    let (mut n, mut exact, mut first_bad) = (0, 0, None);
    for (i, o) in asf.video_objects.iter().enumerate() {
        let p = frames.join(format!("f{i:05}.bin"));
        let Ok(raw) = std::fs::read(&p) else { break };
        let r = dec.decode(&o.data).unwrap();
        let mine = pixel::frame_rgb565(&mut fit, dec.picture(), 256).unwrap();
        let fwf: Vec<u16> = raw.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        let diff = mine.iter().zip(&fwf).filter(|(a, b)| a != b).count();
        n += 1;
        if diff == 0 {
            exact += 1;
        } else if first_bad.is_none() {
            first_bad = Some((i, diff, r));
        }
    }
    eprintln!("sample_movie: {exact}/{n} frames bit-exact vs the firmware dumps; first mismatch {first_bad:?}");
    assert!(n > 0 && exact == n);
}

