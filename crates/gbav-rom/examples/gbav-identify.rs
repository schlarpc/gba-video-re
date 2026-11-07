//! Identify GBA Video ROM files:
//! `cargo run -p gbav-rom --release --example gbav-identify -- FILE...`
//! Prints one tab-separated line per file: file, sha1, No-Intro match, family, engine,
//! build id, streams.

use gbav_rom::{identify, NoIntroMatch};

fn main() {
    for path in std::env::args().skip(1) {
        let rom = match std::fs::read(&path) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("{path}: {e}");
                continue;
            }
        };
        let id = identify(&rom);
        let c = &id.classification;
        let nointro = match &id.nointro {
            NoIntroMatch::Exact(e) => e.name.clone(),
            NoIntroMatch::Overdump { entry, tail } => {
                format!("{} (overdump, tail {tail:?})", entry.name)
            }
            NoIntroMatch::None => "-".into(),
        };
        let fam = match (c.family, c.vx_variant) {
            (Some(f), Some(v)) => format!("{f}/{}", v.as_str()),
            (Some(f), None) => f.to_string(),
            _ => "UNKNOWN".into(),
        };
        let streams: Vec<String> = c
            .streams
            .iter()
            .map(|s| {
                format!(
                    "{}@{:#x} {}x{} {}f",
                    s.id, s.offset, s.width, s.height, s.frames
                )
            })
            .collect();
        println!(
            "{}\t{}\t{}\t{}\t{}\t{}\t{}",
            std::path::Path::new(&path)
                .file_name()
                .map_or(path.clone(), |n| n.to_string_lossy().into_owned()),
            id.sha1,
            nointro,
            fam,
            c.engine().map_or("", |e| e.as_str()),
            c.build_id.as_deref().unwrap_or(""),
            streams.join("; ")
        );
    }
}
