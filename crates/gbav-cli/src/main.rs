//! `gbav`: identify GBA Video carts, list and decode their streams, and verify the decoders
//! against the hash manifests in `verify/`.

// The generic jobs are only reached through dispatch.rs once a codec crate is wired in; until
// then the tests drive them with a mock codec.
#![allow(dead_code)]

mod dispatch;
mod manifest;
mod output;

use dispatch::{dispatch, Job};
use gbav_common::{md5, AudioDecoder, Codec, Error, Frame, Result, StreamInfo, VideoDecoder};
use gbav_rom::{identify, Family, Identification, NoIntroMatch};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "\
usage:
  gbav list ROM
  gbav decode ROM --stream S --out DIR [--start-frame N] [--count M] [--png | --ppm | --raw] [--wav]
  gbav verify ROM|DIR... [--verify-dir DIR] [--no-audio]
  gbav stats ROM

ROMs are your own dumps. verify/ is found through --verify-dir, $GBAV_VERIFY, or the source tree.
--raw writes each frame's bytes exactly as the family's manifest hashes them; --png/--ppm write
the picture the cart shows. --wav writes the stream's native PCM (16-bit mono) from frame 0.";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("list") => cmd_list(&args[1..]),
        Some("decode") => cmd_decode(&args[1..]),
        Some("verify") => cmd_verify(&args[1..]),
        Some("stats") => cmd_stats(&args[1..]),
        Some("-h" | "--help" | "help") => {
            println!("{USAGE}");
            Ok(true)
        }
        _ => Err(Error::Invalid(USAGE.into())),
    };
    match r {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(e) => {
            eprintln!("gbav: {e}");
            ExitCode::from(2)
        }
    }
}

// ------------------------------------------------------------------ argument helpers

struct Args {
    positional: Vec<String>,
    opts: Vec<(String, Option<String>)>,
}

impl Args {
    /// `valued` lists the options that take a value.
    fn parse(args: &[String], valued: &[&str]) -> Result<Args> {
        let mut a = Args { positional: Vec::new(), opts: Vec::new() };
        let mut it = args.iter();
        while let Some(s) = it.next() {
            if let Some(name) = s.strip_prefix("--") {
                if valued.contains(&name) {
                    let v = it.next().ok_or_else(|| Error::Invalid(format!("--{name} needs a value")))?;
                    a.opts.push((name.to_string(), Some(v.clone())));
                } else {
                    a.opts.push((name.to_string(), None));
                }
            } else {
                a.positional.push(s.clone());
            }
        }
        Ok(a)
    }
    fn flag(&self, name: &str) -> bool {
        self.opts.iter().any(|(n, _)| n == name)
    }
    fn value(&self, name: &str) -> Option<&str> {
        self.opts.iter().rev().find(|(n, _)| n == name).and_then(|(_, v)| v.as_deref())
    }
    fn number(&self, name: &str) -> Result<Option<u32>> {
        self.value(name)
            .map(|v| v.parse().map_err(|_| Error::Invalid(format!("--{name}: not a number: {v}"))))
            .transpose()
    }
    fn one_rom(&self) -> Result<PathBuf> {
        match self.positional.as_slice() {
            [p] => Ok(PathBuf::from(p)),
            _ => Err(Error::Invalid(format!("expected one ROM\n{USAGE}"))),
        }
    }
}

fn read_rom(path: &Path) -> Result<Vec<u8>> {
    std::fs::read(path).map_err(|e| Error::Invalid(format!("{}: {e}", path.display())))
}

/// The bytes the codec should see: an overdump is trimmed to the No-Intro size.
fn cart_bytes<'a>(rom: &'a [u8], id: &Identification) -> &'a [u8] {
    match &id.nointro {
        NoIntroMatch::Overdump { entry, .. } => &rom[..entry.size as usize],
        _ => rom,
    }
}

fn family_of(id: &Identification) -> Result<Family> {
    id.classification
        .family
        .ok_or_else(|| Error::Format("not a recognised GBA Video cart (family unknown)".into()))
}

fn fps(s: &StreamInfo) -> f64 {
    s.fps_num as f64 / s.fps_den.max(1) as f64
}

// ------------------------------------------------------------------ jobs

struct Streams;
impl Job for Streams {
    type Out = Vec<StreamInfo>;
    fn run<C: Codec>(self, rom: &[u8]) -> Result<Vec<StreamInfo>> {
        C::streams(rom)
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Format {
    Png,
    Ppm,
    Raw,
}

struct Decode<'a> {
    stream: &'a str,
    start: u32,
    count: Option<u32>,
    out: &'a Path,
    format: Format,
    wav: bool,
}

impl Job for Decode<'_> {
    type Out = (u32, u64);
    fn run<C: Codec>(self, rom: &[u8]) -> Result<(u32, u64)> {
        let info = C::streams(rom)?
            .into_iter()
            .find(|s| s.id == self.stream)
            .ok_or_else(|| Error::Invalid(format!("no stream {:?} (see gbav list)", self.stream)))?;
        std::fs::create_dir_all(self.out).map_err(|e| Error::Invalid(format!("{}: {e}", self.out.display())))?;
        let io = |e: std::io::Error| Error::Invalid(e.to_string());
        let mut dec = C::video(rom, self.stream)?;
        dec.seek(self.start)?;
        let mut n = 0u32;
        while self.count.map_or(true, |c| n < c) {
            let Some(frame) = dec.next_frame()? else { break };
            let k = self.start + n;
            let (name, bytes) = match self.format {
                Format::Raw => (format!("{k:06}.bin"), frame.manifest_bytes().to_vec()),
                Format::Png => {
                    let rgb = output::bgr555_to_rgb8(&frame.bgr555());
                    (format!("{k:06}.png"), output::png_rgb8(info.width, info.height, &rgb))
                }
                Format::Ppm => {
                    let rgb = output::bgr555_to_rgb8(&frame.bgr555());
                    (format!("{k:06}.ppm"), output::ppm_rgb8(info.width, info.height, &rgb))
                }
            };
            std::fs::write(self.out.join(name), bytes).map_err(io)?;
            n += 1;
        }
        let mut samples = 0u64;
        if self.wav {
            let rate = info
                .audio_rate_hz
                .ok_or_else(|| Error::Invalid(format!("stream {} has no audio", self.stream)))?;
            let mut w = output::WavWriter::create(&self.out.join("audio.wav"), rate).map_err(io)?;
            let mut a = C::audio(rom, self.stream)?;
            while a.next_block()?.is_some() {
                w.write(a.last_pcm()).map_err(io)?;
                samples += a.last_pcm().len() as u64;
            }
            w.finish(rate).map_err(io)?;
        }
        Ok((n, samples))
    }
}

/// Result of checking one stream against its manifests.
#[derive(Debug, Default)]
struct Checked {
    stream: String,
    frames_ok: u64,
    frames: u64,
    first_bad_frame: Option<u64>,
    blocks_ok: u64,
    blocks: u64,
    first_bad_block: Option<u64>,
    missing: bool,
}

struct Verify<'a> {
    dir: &'a Path,
    family: &'a str,
    short_id: &'a str,
    audio: bool,
}

impl Job for Verify<'_> {
    type Out = Vec<Checked>;
    fn run<C: Codec>(self, rom: &[u8]) -> Result<Vec<Checked>> {
        let mut out = Vec::new();
        for s in C::streams(rom)? {
            let base = self.dir.join(self.family).join(manifest::stem(self.short_id, &s.id));
            let mut c = Checked { stream: s.id.clone(), ..Default::default() };
            let Some(rows) = manifest::load(&base.with_extension("frame_md5.csv.gz"), manifest::Column::FirstMd5)? else {
                c.missing = true;
                out.push(c);
                continue;
            };
            let want: std::collections::HashMap<u64, &str> =
                rows.iter().filter(|(_, m)| !m.is_empty()).map(|(k, m)| (*k, m.as_str())).collect();
            c.frames = want.len() as u64;
            let mut dec = C::video(rom, &s.id)?;
            dec.seek(0)?;
            let mut k = 0u64;
            while let Some(f) = dec.next_frame()? {
                if let Some(m) = want.get(&k) {
                    if md5::hex(f.manifest_bytes()) == *m {
                        c.frames_ok += 1;
                    } else if c.first_bad_frame.is_none() {
                        c.first_bad_frame = Some(k);
                    }
                }
                k += 1;
            }
            if self.audio {
                if let Some(rows) = manifest::load(&base.with_extension("audio_md5.csv.gz"), manifest::Column::LastMd5)? {
                    c.blocks = rows.len() as u64;
                    let mut a = C::audio(rom, &s.id)?;
                    for (i, (_, m)) in rows.iter().enumerate() {
                        let Some(b) = a.next_block()? else { break };
                        if md5::hex(&b) == *m {
                            c.blocks_ok += 1;
                        } else if c.first_bad_block.is_none() {
                            c.first_bad_block = Some(i as u64);
                        }
                    }
                }
            }
            out.push(c);
        }
        Ok(out)
    }
}

// ------------------------------------------------------------------ commands

fn describe(id: &Identification) -> String {
    let mut s = format!("sha1      {}\nsize      {} bytes\n", id.sha1, id.size);
    match &id.nointro {
        NoIntroMatch::Exact(e) => s += &format!("No-Intro  {}\n", e.name),
        NoIntroMatch::Overdump { entry, tail } => {
            s += &format!("No-Intro  {} (overdump: first {} bytes match, tail is {tail:?})\n", entry.name, entry.size)
        }
        NoIntroMatch::None => s += "No-Intro  no match\n",
    }
    let c = &id.classification;
    s += &format!(
        "family    {}{}\nengine    {}\nbuild     {}\n",
        c.family.map_or("unknown", |f| f.as_str()),
        c.vx_variant.map(|v| format!(" ({})", v.as_str())).unwrap_or_default(),
        c.engine().map_or("unknown", |e| e.as_str()),
        c.build_id.as_deref().unwrap_or("unknown"),
    );
    if let Some(e) = id.nointro.entry() {
        if !e.notes.is_empty() {
            s += &format!("notes     {}\n", e.notes);
        }
    }
    s
}

fn cmd_list(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &[])?;
    let rom = read_rom(&a.one_rom()?)?;
    let id = identify(&rom);
    print!("{}", describe(&id));
    let Ok(family) = family_of(&id) else { return Ok(true) };
    match dispatch(family, cart_bytes(&rom, &id), Streams) {
        Ok(streams) => {
            println!("streams   {}", streams.len());
            for s in &streams {
                println!(
                    "  {:<14} {}x{} {:>7.4} fps {:>7} frames {:>9.1} s  seek points {:>4}  audio {}{}",
                    s.id,
                    s.width,
                    s.height,
                    fps(s),
                    s.frames,
                    s.frames as f64 / fps(s),
                    s.seek_points.len(),
                    s.audio_rate_hz.map_or("none".into(), |r| format!("{r} Hz")),
                    s.title.as_deref().map(|t| format!("  \"{t}\"")).unwrap_or_default(),
                );
            }
        }
        Err(e) => {
            // Without the codec crate, show what the container parser found.
            println!("streams   {} (container scan only: {e})", id.classification.streams.len());
            for s in &id.classification.streams {
                println!("  {:<14} {}x{} {:>7} frames  at ROM {:#x}", s.id, s.width, s.height, s.frames, s.offset);
            }
        }
    }
    Ok(true)
}

fn cmd_decode(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["stream", "out", "start-frame", "count"])?;
    let rom = read_rom(&a.one_rom()?)?;
    let id = identify(&rom);
    let format = match (a.flag("png"), a.flag("ppm"), a.flag("raw")) {
        (_, false, false) => Format::Png,
        (false, true, false) => Format::Ppm,
        (false, false, true) => Format::Raw,
        _ => return Err(Error::Invalid("choose one of --png, --ppm, --raw".into())),
    };
    let out = PathBuf::from(a.value("out").ok_or_else(|| Error::Invalid("--out DIR is required".into()))?);
    let job = Decode {
        stream: a.value("stream").ok_or_else(|| Error::Invalid("--stream S is required".into()))?,
        start: a.number("start-frame")?.unwrap_or(0),
        count: a.number("count")?,
        out: &out,
        format,
        wav: a.flag("wav"),
    };
    let (frames, samples) = dispatch(family_of(&id)?, cart_bytes(&rom, &id), job)?;
    println!("{frames} frames{} written to {}", if samples > 0 { format!(", {samples} samples") } else { String::new() }, out.display());
    Ok(true)
}

fn rom_paths(args: &[String]) -> Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    for p in args {
        let p = PathBuf::from(p);
        if p.is_dir() {
            let mut v: Vec<PathBuf> = std::fs::read_dir(&p)
                .map_err(|e| Error::Invalid(format!("{}: {e}", p.display())))?
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|q| q.extension().is_some_and(|x| x.eq_ignore_ascii_case("gba")))
                .collect();
            v.sort();
            out.extend(v);
        } else {
            out.push(p);
        }
    }
    Ok(out)
}

fn cmd_verify(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["verify-dir"])?;
    if a.positional.is_empty() {
        return Err(Error::Invalid(format!("expected ROMs or a directory\n{USAGE}")));
    }
    let dir = manifest::verify_dir(a.value("verify-dir").map(Path::new))?;
    let carts = manifest::carts(&dir)?;
    let (mut all_ok, mut tf, mut tfo, mut tb, mut tbo, mut nstreams) = (true, 0, 0, 0, 0, 0);
    for path in rom_paths(&a.positional)? {
        let rom = read_rom(&path)?;
        let id = identify(&rom);
        let sha = id.nointro.entry().map_or(id.sha1.as_str(), |e| e.sha1.as_str());
        let Some(cart) = carts.iter().find(|c| c.sha1 == sha) else {
            println!("SKIP  {}: not a cart with manifests", path.display());
            continue;
        };
        let job = Verify { dir: &dir, family: &cart.family, short_id: &cart.short_id, audio: !a.flag("no-audio") };
        let res = match family_of(&id).and_then(|f| dispatch(f, cart_bytes(&rom, &id), job)) {
            Ok(r) => r,
            Err(e) => {
                println!("FAIL  {}: {e}", cart.short_id);
                all_ok = false;
                continue;
            }
        };
        for c in res {
            if c.missing {
                println!("--    {:<14} {:<14} no manifest", cart.short_id, c.stream);
                continue;
            }
            let ok = c.frames_ok == c.frames && c.blocks_ok == c.blocks;
            all_ok &= ok;
            nstreams += 1;
            tf += c.frames;
            tfo += c.frames_ok;
            tb += c.blocks;
            tbo += c.blocks_ok;
            println!(
                "{}  {:<14} {:<14} video {}/{}{}  audio {}/{}{}",
                if ok { "OK  " } else { "FAIL" },
                cart.short_id,
                c.stream,
                c.frames_ok,
                c.frames,
                c.first_bad_frame.map(|f| format!(" (first bad frame {f})")).unwrap_or_default(),
                c.blocks_ok,
                c.blocks,
                c.first_bad_block.map(|b| format!(" (first bad block {b})")).unwrap_or_default(),
            );
        }
    }
    println!("TOTAL {nstreams} streams: video {tfo}/{tf} frames, audio {tbo}/{tb} blocks");
    Ok(all_ok)
}

fn cmd_stats(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &[])?;
    let rom = read_rom(&a.one_rom()?)?;
    let id = identify(&rom);
    print!("{}", describe(&id));
    let streams = dispatch(family_of(&id)?, cart_bytes(&rom, &id), Streams)?;
    println!("stream\twidth\theight\tfps\tframes\tduration_s\tseek_points\taudio_rate_hz\ttitle");
    let mut total = 0.0;
    for s in &streams {
        let d = s.frames as f64 / fps(s);
        total += d;
        println!(
            "{}\t{}\t{}\t{:.5}\t{}\t{:.2}\t{}\t{}\t{}",
            s.id,
            s.width,
            s.height,
            fps(s),
            s.frames,
            d,
            s.seek_points.len(),
            s.audio_rate_hz.map_or("-".into(), |r| r.to_string()),
            s.title.as_deref().unwrap_or("")
        );
    }
    println!("total\t\t\t\t{}\t{total:.2}", streams.iter().map(|s| s.frames as u64).sum::<u64>());
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn args() {
        let v: Vec<String> = ["rom.gba", "--stream", "0", "--png", "--count", "5"].iter().map(|s| s.to_string()).collect();
        let a = Args::parse(&v, &["stream", "count"]).unwrap();
        assert_eq!(a.positional, ["rom.gba"]);
        assert_eq!(a.value("stream"), Some("0"));
        assert_eq!(a.number("count").unwrap(), Some(5));
        assert!(a.flag("png") && !a.flag("raw"));
        assert!(Args::parse(&["--stream".to_string()], &["stream"]).is_err());
    }

    // A two-stream mock codec: frame k of a stream is 2x2 pixels of value k; audio block k is 4
    // bytes of k with PCM [k, -k].
    struct Mock;
    struct MockFrame(Vec<u8>);
    impl Frame for MockFrame {
        fn manifest_bytes(&self) -> &[u8] {
            &self.0
        }
        fn bgr555(&self) -> Vec<u16> {
            vec![self.0[0] as u16; 4]
        }
    }
    struct MockVideo(u32, u32);
    impl VideoDecoder for MockVideo {
        type Frame = MockFrame;
        fn seek(&mut self, f: u32) -> Result<()> {
            self.0 = f;
            Ok(())
        }
        fn next_frame(&mut self) -> Result<Option<MockFrame>> {
            if self.0 >= self.1 {
                return Ok(None);
            }
            self.0 += 1;
            Ok(Some(MockFrame(vec![(self.0 - 1) as u8; 4])))
        }
    }
    struct MockAudio(u32, [i16; 2]);
    impl AudioDecoder for MockAudio {
        fn next_block(&mut self) -> Result<Option<Vec<u8>>> {
            if self.0 >= 2 {
                return Ok(None);
            }
            self.1 = [self.0 as i16, -(self.0 as i16)];
            self.0 += 1;
            Ok(Some(vec![(self.0 - 1) as u8; 4]))
        }
        fn last_pcm(&self) -> &[i16] {
            &self.1
        }
    }
    impl Codec for Mock {
        const FAMILY: &'static str = "mock";
        type Video = MockVideo;
        type Audio = MockAudio;
        fn streams(_rom: &[u8]) -> Result<Vec<StreamInfo>> {
            Ok(["s0", "s 1"]
                .iter()
                .map(|id| StreamInfo {
                    id: id.to_string(),
                    title: None,
                    width: 2,
                    height: 2,
                    fps_num: 12,
                    fps_den: 1,
                    frames: 3,
                    seek_points: vec![0],
                    audio_rate_hz: Some(16384),
                })
                .collect())
        }
        fn video(_rom: &[u8], _s: &str) -> Result<MockVideo> {
            Ok(MockVideo(0, 3))
        }
        fn audio(_rom: &[u8], _s: &str) -> Result<MockAudio> {
            Ok(MockAudio(0, [0; 2]))
        }
    }

    fn gz(text: &str) -> Vec<u8> {
        let z = gbav_common::gzip::zlib_stored(text.as_bytes());
        let mut g = vec![0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3];
        g.extend_from_slice(&z[2..z.len() - 4]);
        g.extend_from_slice(&gbav_common::gzip::crc32(0, text.as_bytes()).to_le_bytes());
        g.extend_from_slice(&(text.len() as u32).to_le_bytes());
        g
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("gbav-cli-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn decode_job_writes_frames_and_wav() {
        let out = scratch("decode");
        let job = Decode { stream: "s0", start: 1, count: Some(5), out: &out, format: Format::Png, wav: true };
        let (n, samples) = job.run::<Mock>(&[]).unwrap();
        assert_eq!((n, samples), (2, 4));
        let png = std::fs::read(out.join("000001.png")).unwrap();
        assert_eq!(&png[1..4], b"PNG");
        let wav = std::fs::read(out.join("audio.wav")).unwrap();
        assert_eq!(wav.len(), 44 + 8);
        assert_eq!(&wav[44..], &[0, 0, 0, 0, 1, 0, 0xff, 0xff]);
        let raw = Decode { stream: "s0", start: 0, count: None, out: &out, format: Format::Raw, wav: false };
        assert_eq!(raw.run::<Mock>(&[]).unwrap().0, 3);
        assert_eq!(std::fs::read(out.join("000002.bin")).unwrap(), [2; 4]);
        let _ = std::fs::remove_dir_all(&out);
    }

    #[test]
    fn verify_job_against_manifests() {
        let dir = scratch("verify");
        std::fs::create_dir_all(dir.join("mock")).unwrap();
        let h = |k: u8| md5::hex(&[k; 4]);
        // stream s0: all frames and blocks match; stream "s 1": frame 2 and block 0 are wrong
        let f0 = format!("frame,md5,other\n0,{},x\n1,{},x\n2,{},x\n", h(0), h(1), h(2));
        let a0 = format!("block,n,md5\n0,4,{}\n1,4,{}\n", h(0), h(1));
        let f1 = format!("frame,md5\n0,{}\n1,{}\n2,{}\n", h(0), h(1), h(9));
        let a1 = format!("frame,block,md5_a,md5_b\n0,0,x,{}\n0,1,x,{}\n", h(7), h(1));
        for (name, text) in [("cart_s0.frame", &f0), ("cart_s0.audio", &a0), ("cart_s_1.frame", &f1), ("cart_s_1.audio", &a1)] {
            std::fs::write(dir.join("mock").join(format!("{name}_md5.csv.gz")), gz(text)).unwrap();
        }
        let res = Verify { dir: &dir, family: "mock", short_id: "cart", audio: true }.run::<Mock>(&[]).unwrap();
        assert_eq!((res[0].frames_ok, res[0].frames, res[0].blocks_ok, res[0].blocks), (3, 3, 2, 2));
        assert_eq!((res[1].frames_ok, res[1].frames, res[1].first_bad_frame), (2, 3, Some(2)));
        assert_eq!((res[1].blocks_ok, res[1].blocks, res[1].first_bad_block), (1, 2, Some(0)));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
