//! Identify GBA Video ROM files: `cargo run -p gbav-rom --release --example gbav-identify -- FILE...`
//! Prints one tab-separated line per file: file, sha1, family, engine, build id, streams.

use gbav_rom::classify;

fn main() {
    for path in std::env::args().skip(1) {
        let rom = match std::fs::read(&path) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("{path}: {e}");
                continue;
            }
        };
        let c = classify(&rom);
        let fam = match (c.family, c.vx_variant) {
            (Some(f), Some(v)) => format!("{f}/{}", v.as_str()),
            (Some(f), None) => f.to_string(),
            _ => "UNKNOWN".into(),
        };
        let streams: Vec<String> = c
            .streams
            .iter()
            .map(|s| format!("{}@{:#x} {}x{} {}f", s.id, s.offset, s.width, s.height, s.frames))
            .collect();
        println!(
            "{}\t{}\t{}\t{}\t{}\t{}",
            std::path::Path::new(&path).file_name().unwrap().to_string_lossy(),
            gbav_rom::sha1::hex_digest(&rom),
            fam,
            c.engine().map_or("", |e| e.as_str()),
            c.build_id.as_deref().unwrap_or(""),
            streams.join("; ")
        );
    }
}
