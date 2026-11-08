//! AAC (Play-Yan Micro / playan.fup video firmware) on Nintendo's MP4 sample.
//! Needs `PY_ROMS` (the Play-Yan Micro ROM and/or playan.fup) and `PY_MEDIA` (Nintendo's sample
//! files, laid out as in docs/playyan/decoders.md); skipped without them.
//!
//! The expected md5 is of the reported headphone PCM of every frame (1024 stereo int16 frames
//! each, the first two frames report none), as the firmware decodes it in the VC01 emulator
//! (tools/playyan/aac/cmp_c.py: all 3,201 frames identical to the C reference, which this matches).

use gbav_common::md5::Md5;
use gbav_playyan::aac::Decoder;
use gbav_playyan::mp4::Mp4;
use gbav_playyan::{Firmware, ImageId};
use std::path::PathBuf;

fn env_dir(k: &str) -> Option<PathBuf> {
    let d = PathBuf::from(std::env::var_os(k)?);
    d.is_dir().then_some(d)
}

const SAMPLE: &str = "play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4";
const SAMPLE_PCM_MD5: &str = "4c6363007939b678b490e66c99f7c6d5";

fn firmware() -> Option<Firmware> {
    let roms = gbav_testkit::playyan_roms()?;
    let files: Vec<PathBuf> = ["Play-Yan Micro (Japan).gba", "playan.fup"].iter().map(|f| roms.join(f)).filter(|p| p.is_file()).collect();
    if files.is_empty() {
        return None;
    }
    Firmware::from_paths(&files).ok()
}

#[test]
fn sample_movie_audio_matches_firmware() {
    let (Some(fw), Some(media)) = (firmware(), env_dir("PY_MEDIA")) else {
        return eprintln!("PY_ROMS / PY_MEDIA not set; skipped");
    };
    let data = std::fs::read(media.join(SAMPLE)).unwrap();
    let mp4 = Mp4::parse(&data).unwrap();
    let tr = mp4.audio().expect("audio track");
    assert_eq!(tr.samples.len(), 3201);
    for id in [ImageId::MiFw0, ImageId::FupSec0] {
        let Some(img) = fw.image(id) else { continue };
        let mut d = Decoder::new(img, tr.config()).unwrap();
        assert_eq!(d.sample_rate(), 44100);
        let mut h = Md5::new();
        let mut n = 0;
        for i in 0..tr.samples.len() {
            let pcm = d.decode_frame(tr.sample_data(&data, i).unwrap()).unwrap();
            if !pcm.is_empty() {
                h.update_u16(&pcm.iter().map(|&v| v as u16).collect::<Vec<_>>());
                n += 1;
            }
        }
        assert_eq!(n, 3199);
        assert_eq!(h.finalize_hex(), SAMPLE_PCM_MD5, "{}", id.name());
    }
}

#[test]
fn sample_movie_container() {
    let Some(media) = env_dir("PY_MEDIA") else { return eprintln!("PY_MEDIA not set; skipped") };
    let data = std::fs::read(media.join(SAMPLE)).unwrap();
    let mp4 = Mp4::parse(&data).unwrap();
    let v = mp4.video().expect("video track");
    match &v.codec {
        gbav_playyan::mp4::Codec::Mp4v { width, height, config } => {
            assert_eq!((*width, *height), (240, 176));
            assert!(config.starts_with(&[0, 0, 1]), "VOL headers expected in the esds");
        }
        c => panic!("{c:?}"),
    }
    assert_eq!(v.samples.len(), 2228);
    assert!(v.samples[0].sync);
    let a = mp4.audio().unwrap();
    assert_eq!(a.config(), &[0x12, 0x10]);
}
