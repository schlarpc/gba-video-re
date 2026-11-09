//! `gbav`: identify GBA Video carts, list and decode their streams, and verify the decoders
//! against the hash manifests in `verify/`.

mod dispatch;
mod output;
mod playyan;

use dispatch::{dispatch, Job};
use gbav_common::manifest::{self, Check, Known, Manifest, StreamManifests};
use gbav_common::{AudioDecoder, Codec, Error, Frame, Result, Rom, StreamInfo, VideoDecoder};
use gbav_rom::{identify, Family, Identification, NoIntroMatch};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::Arc;

const USAGE: &str = "\
usage:
  gbav list ROM
  gbav decode ROM --stream S --out DIR [--start-frame N] [--count M] [--png | --ppm | --raw] [--wav]
  gbav list MEDIA --firmware FW [--firmware FW]                    (Play-Yan: ASF / MP4 / MP3 + your ROM or playan.fup)
  gbav decode MEDIA --firmware FW [--firmware FW] --out DIR [--png | --ppm | --raw] [--wav] [--gba-audio]
              [--brightness N] [--volume N] [--start-frame N] [--count M] [--image NAME]
  gbav verify MEDIA... --firmware FW [--firmware FW] --manifests DIR [--volume N] [--prev-rate HZ]
  gbav verify ROM|DIR... [--no-audio] [--jobs N]
  gbav stats ROM
  gbav make-segments ROM|DIR... [--seconds S]     (maintainers: write verify/<family>/*.audio_seg_md5.csv.gz)

ROMs are your own dumps. verify/ is found through $GBAV_VERIFY or the source tree.
--start-frame must be a seek point. --raw writes each frame's bytes exactly as the family's
manifest hashes them; --png/--ppm write the picture the cart shows. --wav writes the stream's
native PCM from the start, as 16-bit mono (values beyond 16 bits saturate).";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("list") if playyan::wants(&args[1..]) => playyan::cmd_list(&args[1..]),
        Some("decode") if playyan::wants(&args[1..]) => playyan::cmd_decode(&args[1..]),
        Some("verify") if playyan::wants(&args[1..]) => playyan::cmd_verify(&args[1..]),
        Some("list") => cmd_list(&args[1..]),
        Some("decode") => cmd_decode(&args[1..]),
        Some("verify") => cmd_verify(&args[1..]),
        Some("stats") => cmd_stats(&args[1..]),
        Some("make-segments") => cmd_make_segments(&args[1..]),
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

/// A ROM file: its identification and the bytes the codec sees (an overdump is trimmed to the
/// No-Intro size).
struct Cart {
    id: Identification,
    rom: Rom,
}

fn load(path: &Path) -> Result<Cart> {
    let mut bytes = std::fs::read(path).map_err(|e| Error::Invalid(format!("{}: {e}", path.display())))?;
    let id = identify(&bytes);
    if let NoIntroMatch::Overdump { entry, .. } = &id.nointro {
        bytes.truncate(entry.size as usize);
    }
    Ok(Cart { id, rom: Arc::from(bytes) })
}

impl Cart {
    fn family(&self) -> Result<Family> {
        self.id
            .classification
            .family
            .ok_or_else(|| Error::Format("not a recognised GBA Video cart (family unknown)".into()))
    }
    /// sha1 of the cart data (the No-Intro entry's for an overdump).
    fn sha1(&self) -> &str {
        self.id.nointro.entry().map_or(self.id.sha1.as_str(), |e| e.sha1.as_str())
    }
    fn run<J: Job>(&self, job: J) -> Result<J::Out> {
        dispatch(self.family()?, &self.rom, job)
    }
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

fn verify_dir() -> Result<PathBuf> {
    manifest::verify_dir().ok_or_else(|| Error::Invalid("no verify/ directory with carts.csv; set GBAV_VERIFY".into()))
}

/// Run `f` over `items` on `jobs` threads; results in input order.
fn par_map<T: Sync, R: Send>(items: &[T], jobs: usize, f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let next = std::sync::atomic::AtomicUsize::new(0);
    let slots: Vec<std::sync::Mutex<Option<R>>> = items.iter().map(|_| std::sync::Mutex::new(None)).collect();
    std::thread::scope(|s| {
        for _ in 0..jobs.clamp(1, items.len().max(1)) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                if i >= items.len() {
                    break;
                }
                *slots[i].lock().unwrap() = Some(f(&items[i]));
            });
        }
    });
    slots.into_iter().map(|m| m.into_inner().unwrap().unwrap()).collect()
}

// ------------------------------------------------------------------ jobs

struct Streams;
impl Job for Streams {
    type Out = Vec<StreamInfo>;
    fn run<C: Codec>(self, rom: &Rom) -> Result<Vec<StreamInfo>> {
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
    fn run<C: Codec>(self, rom: &Rom) -> Result<(u32, u64)> {
        let info = C::streams(rom)?
            .into_iter()
            .find(|s| s.id == self.stream)
            .ok_or_else(|| Error::Invalid(format!("no stream {:?} (see gbav list)", self.stream)))?;
        std::fs::create_dir_all(self.out).map_err(|e| Error::Invalid(format!("{}: {e}", self.out.display())))?;
        let io = |e: std::io::Error| Error::Invalid(e.to_string());
        let mut dec = C::video(rom, self.stream)?;
        dec.seek(self.start)?;
        let mut n = 0u32;
        let mut px = Vec::new();
        while self.count.is_none_or(|c| n < c) {
            let Some(frame) = dec.next_frame()? else { break };
            let k = self.start + n;
            let (w, h) = frame.size();
            let (name, bytes) = match self.format {
                Format::Raw => (format!("{k:06}.bin"), frame.manifest_bytes().to_vec()),
                Format::Png | Format::Ppm => {
                    px.resize((w * h) as usize, 0);
                    frame.bgr555_into(&mut px);
                    let rgb = output::bgr555_to_rgb8(&px);
                    if self.format == Format::Png {
                        (format!("{k:06}.png"), output::png_rgb8(w, h, &rgb))
                    } else {
                        (format!("{k:06}.ppm"), output::ppm_rgb8(w, h, &rgb))
                    }
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
            while a.next_block()? {
                w.write(a.pcm()).map_err(io)?;
                samples += a.pcm().len() as u64;
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
    video: Option<Check>,
    audio: Option<Check>,
}

impl Checked {
    fn passed(&self) -> bool {
        self.video.as_ref().is_none_or(Check::passed) && self.audio.as_ref().is_none_or(Check::passed)
    }
}

struct Verify<'a> {
    dir: &'a Path,
    family: &'a str,
    short_id: &'a str,
    known: &'a [Known],
    audio: bool,
}

impl Job for Verify<'_> {
    type Out = Vec<Checked>;
    fn run<C: Codec>(self, rom: &Rom) -> Result<Vec<Checked>> {
        let mut out = Vec::new();
        for s in C::streams(rom)? {
            let paths = StreamManifests::new(self.dir, self.family, self.short_id, &s.id);
            let sid = s.id.replace(' ', "_");
            let known: Vec<&Known> = self.known.iter().filter(|k| k.short_id == self.short_id && k.stream == sid).collect();
            let mut c = Checked { stream: s.id.clone(), ..Default::default() };
            if let Some(m) = Manifest::load(&paths.frames)? {
                let mut dec = C::video(rom, &s.id)?;
                c.video = Some(manifest::check_video(&mut dec, &m, &known)?);
            }
            if self.audio && s.audio_rate_hz.is_some() {
                c.audio = manifest::check_audio(|| C::audio(rom, &s.id), &paths, &known)?;
            }
            out.push(c);
        }
        Ok(out)
    }
}

struct MakeSegments<'a> {
    dir: &'a Path,
    known: &'a [Known],
    family: &'a str,
    short_id: &'a str,
    seconds: u32,
}

impl Job for MakeSegments<'_> {
    type Out = Vec<(String, Check, usize)>;
    fn run<C: Codec>(self, rom: &Rom) -> Result<Self::Out> {
        let mut out = Vec::new();
        for s in C::streams(rom)? {
            let paths = StreamManifests::new(self.dir, self.family, self.short_id, &s.id);
            let (Some(rate), Some(m)) = (s.audio_rate_hz, Manifest::load(&paths.audio)?) else { continue };
            let sid = s.id.replace(' ', "_");
            let known: Vec<&Known> = self.known.iter().filter(|k| k.short_id == self.short_id && k.stream == sid).collect();
            let (seg, check) = manifest::make_segments(&mut C::audio(rom, &s.id)?, &m, &known, rate, self.seconds)?;
            std::fs::write(&paths.audio_seg, seg.to_gzip())
                .map_err(|e| Error::Invalid(format!("{}: {e}", paths.audio_seg.display())))?;
            out.push((s.id, check, seg.rows.len()));
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
    let cart = load(&a.one_rom()?)?;
    print!("{}", describe(&cart.id));
    if cart.family().is_err() {
        return Ok(true);
    }
    match cart.run(Streams) {
        Ok(streams) => {
            println!("streams   {}", streams.len());
            for s in &streams {
                println!(
                    "  {:<14} {}x{} {:>7.4} fps {:>7} frames {:>9.1} s  seek points {:>4}  chapters {:>2}  audio {}{}",
                    s.id,
                    s.width,
                    s.height,
                    s.fps(),
                    s.frames,
                    s.frames as f64 / s.fps(),
                    s.seek_points.len(),
                    s.chapters.len(),
                    s.audio_rate_hz.map_or("none".into(), |r| format!("{r} Hz")),
                    s.title.as_deref().map(|t| format!("  \"{t}\"")).unwrap_or_default(),
                );
            }
        }
        Err(e) => {
            // Without the codec, show what the container parser found.
            println!("streams   {} (container scan only: {e})", cart.id.classification.streams.len());
            for s in &cart.id.classification.streams {
                println!("  {:<14} {}x{} {:>7} frames  at ROM {:#x}", s.id, s.width, s.height, s.frames, s.offset);
            }
        }
    }
    Ok(true)
}

fn cmd_decode(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["stream", "out", "start-frame", "count"])?;
    let cart = load(&a.one_rom()?)?;
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
    let (frames, samples) = cart.run(job)?;
    let extra = if samples > 0 { format!(", {samples} samples") } else { String::new() };
    println!("{frames} frames{extra} written to {}", out.display());
    Ok(true)
}

fn default_jobs() -> usize {
    std::thread::available_parallelism().map_or(4, |n| n.get())
}

fn cmd_verify(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["jobs"])?;
    if a.positional.is_empty() {
        return Err(Error::Invalid(format!("expected ROMs or a directory\n{USAGE}")));
    }
    let dir = verify_dir()?;
    let carts = manifest::carts(&dir)?;
    let known = manifest::known(&dir)?;
    let jobs = a.number("jobs")?.map_or_else(default_jobs, |j| j as usize);
    let paths = rom_paths(&a.positional)?;
    let started = std::time::Instant::now();
    let results = par_map(&paths, jobs, |path| -> (String, Result<Vec<Checked>>) {
        let cart = match load(path) {
            Ok(c) => c,
            Err(e) => return (path.display().to_string(), Err(e)),
        };
        let Some(entry) = carts.iter().find(|c| c.sha1 == cart.sha1()) else {
            return (path.display().to_string(), Ok(Vec::new()));
        };
        let job = Verify { dir: &dir, family: &entry.family, short_id: &entry.short_id, known: &known, audio: !a.flag("no-audio") };
        (format!("{}:{}", entry.family, entry.short_id), cart.run(job))
    });
    let mut all_ok = true;
    let mut fam: std::collections::BTreeMap<String, [u64; 7]> = Default::default();
    for (name, r) in results {
        let res = match r {
            Ok(v) if v.is_empty() => {
                println!("SKIP  {name}: not a cart with manifests");
                continue;
            }
            Ok(v) => v,
            Err(e) => {
                println!("FAIL  {name}: {e}");
                all_ok = false;
                continue;
            }
        };
        let family = name.split(':').next().unwrap_or("").to_string();
        let t = fam.entry(family).or_default();
        t[0] += 1;
        for c in res {
            let ok = c.passed();
            all_ok &= ok;
            t[1] += 1;
            if let Some(v) = &c.video {
                t[2] += v.ok;
                t[3] += v.total;
                t[6] += v.known_bad;
            }
            if let Some(au) = &c.audio {
                t[4] += au.ok;
                t[5] += au.total;
                t[6] += au.known_bad;
            }
            let show = |c: &Option<Check>| c.as_ref().map_or("no manifest".to_string(), |c| c.to_string());
            println!(
                "{}  {:<26} {:<14} video {}  audio {}",
                if ok { "OK  " } else { "FAIL" },
                name,
                c.stream,
                show(&c.video),
                show(&c.audio)
            );
        }
    }
    println!();
    println!("family     carts streams  video frames ok/total     audio units ok/total   known cart-side");
    for (f, t) in &fam {
        println!("{f:<10} {:>5} {:>7}  {:>10}/{:<10}  {:>10}/{:<10} {:>8}", t[0], t[1], t[2], t[3], t[4], t[5], t[6]);
    }
    println!("{} in {:.0} s", if all_ok { "ALL OK" } else { "FAILURES" }, started.elapsed().as_secs_f64());
    Ok(all_ok)
}

fn cmd_stats(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &[])?;
    let cart = load(&a.one_rom()?)?;
    print!("{}", describe(&cart.id));
    let streams = cart.run(Streams)?;
    println!("stream\twidth\theight\tfps\tframes\tduration_s\tseek_points\tchapters\taudio_rate_hz\ttitle");
    let mut total = 0.0;
    for s in &streams {
        let d = s.frames as f64 / s.fps();
        total += d;
        println!(
            "{}\t{}\t{}\t{:.5}\t{}\t{:.2}\t{}\t{}\t{}\t{}",
            s.id,
            s.width,
            s.height,
            s.fps(),
            s.frames,
            d,
            s.seek_points.len(),
            s.chapters.len(),
            s.audio_rate_hz.map_or("-".into(), |r| r.to_string()),
            s.title.as_deref().unwrap_or("")
        );
    }
    println!("total\t\t\t\t{}\t{total:.2}", streams.iter().map(|s| s.frames as u64).sum::<u64>());
    Ok(true)
}

fn cmd_make_segments(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["seconds", "jobs"])?;
    let dir = verify_dir()?;
    let carts = manifest::carts(&dir)?;
    let known = manifest::known(&dir)?;
    let seconds = a.number("seconds")?.unwrap_or(30);
    let jobs = a.number("jobs")?.map_or_else(default_jobs, |j| j as usize);
    let paths = rom_paths(&a.positional)?;
    let results = par_map(&paths, jobs, |path| -> Result<Option<(String, Vec<(String, Check, usize)>)>> {
        let cart = load(path)?;
        let Some(entry) = carts.iter().find(|c| c.sha1 == cart.sha1()) else { return Ok(None) };
        let job = MakeSegments { dir: &dir, known: &known, family: &entry.family, short_id: &entry.short_id, seconds };
        Ok(Some((entry.short_id.clone(), cart.run(job)?)))
    });
    let mut ok = true;
    for r in results {
        match r {
            Ok(Some((id, v))) => {
                for (s, c, n) in v {
                    println!("{id:<22} {s:<14} {n:>4} segments from {c}");
                }
            }
            Ok(None) => {}
            Err(e) => {
                println!("FAIL  {e}");
                ok = false;
            }
        }
    }
    Ok(ok)
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
        fn size(&self) -> (u32, u32) {
            (2, 2)
        }
        fn bgr555_into(&self, out: &mut [u16]) {
            out.fill(self.0[0] as u16);
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
    struct MockAudio(u32, [i32; 2], [u8; 4]);
    impl AudioDecoder for MockAudio {
        fn next_block(&mut self) -> Result<bool> {
            if self.0 >= 2 {
                return Ok(false);
            }
            self.1 = [self.0 as i32, -(self.0 as i32)];
            self.2 = [self.0 as u8; 4];
            self.0 += 1;
            Ok(true)
        }
        fn key(&self) -> gbav_common::BlockKey {
            gbav_common::BlockKey { frame: None, index: self.0 as u64 - 1 }
        }
        fn manifest_part(&self, _i: usize) -> &[u8] {
            &self.2
        }
        fn pcm(&self) -> &[i32] {
            &self.1
        }
        fn seek(&mut self, _f: u32) -> Result<()> {
            Ok(())
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
                    chapters: vec![],
                    audio_rate_hz: Some(16384),
                })
                .collect())
        }
        fn video(_rom: &Rom, _s: &str) -> Result<MockVideo> {
            Ok(MockVideo(0, 3))
        }
        fn audio(_rom: &Rom, _s: &str) -> Result<MockAudio> {
            Ok(MockAudio(0, [0; 2], [0; 4]))
        }
    }

    fn rom() -> Rom {
        Arc::from(Vec::new())
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
        let (n, samples) = job.run::<Mock>(&rom()).unwrap();
        assert_eq!((n, samples), (2, 4));
        let png = std::fs::read(out.join("000001.png")).unwrap();
        assert_eq!(&png[1..4], b"PNG");
        let wav = std::fs::read(out.join("audio.wav")).unwrap();
        assert_eq!(wav.len(), 44 + 8);
        assert_eq!(&wav[44..], &[0, 0, 0, 0, 1, 0, 0xff, 0xff]);
        let raw = Decode { stream: "s0", start: 0, count: None, out: &out, format: Format::Raw, wav: false };
        assert_eq!(raw.run::<Mock>(&rom()).unwrap().0, 3);
        assert_eq!(std::fs::read(out.join("000002.bin")).unwrap(), [2; 4]);
        let _ = std::fs::remove_dir_all(&out);
    }

    #[test]
    fn verify_and_segment_jobs() {
        use gbav_common::{gzip::gzip_stored, md5};
        let dir = scratch("verify");
        std::fs::create_dir_all(dir.join("mock")).unwrap();
        let h = |k: u8| md5::hex(&[k; 4]);
        // stream s0: all frames and blocks match; stream "s 1": frame 2 and block 0 are wrong,
        // and block 0 is a known cart-side difference
        let f0 = format!("frame,md5,other\n0,{},x\n1,{},x\n2,{},x\n", h(0), h(1), h(2));
        let a0 = format!("block,md5\n0,{}\n1,{}\n", h(0), h(1));
        let f1 = format!("frame,md5\n0,{}\n1,{}\n2,{}\n", h(0), h(1), h(9));
        let a1 = format!("block,md5\n0,{}\n1,{}\n", h(7), h(1));
        for (name, text) in [("cart_s0.frame", &f0), ("cart_s0.audio", &a0), ("cart_s_1.frame", &f1), ("cart_s_1.audio", &a1)] {
            std::fs::write(dir.join("mock").join(format!("{name}_md5.csv.gz")), gzip_stored(text.as_bytes())).unwrap();
        }
        let known = vec![Known {
            family: "mock".into(),
            short_id: "cart".into(),
            stream: "s_1".into(),
            kind: "audio".into(),
            first: 0,
            last: Some(0),
            reason: String::new(),
        }];
        let res = Verify { dir: &dir, family: "mock", short_id: "cart", known: &known, audio: true }.run::<Mock>(&rom()).unwrap();
        let (v0, a0) = (res[0].video.clone().unwrap(), res[0].audio.clone().unwrap());
        assert!(v0.passed() && a0.passed() && (v0.ok, a0.ok) == (3, 2));
        let (v1, a1) = (res[1].video.clone().unwrap(), res[1].audio.clone().unwrap());
        assert_eq!((v1.ok, v1.total, v1.first_bad), (2, 3, Some(2)));
        assert_eq!((a1.ok, a1.known_bad, a1.first_bad), (1, 1, None));
        assert!(a1.passed() && !v1.passed());
        // segment manifests replace the per-block ones
        let made = MakeSegments { dir: &dir, known: &[], family: "mock", short_id: "cart", seconds: 30 }.run::<Mock>(&rom()).unwrap();
        assert_eq!(made.len(), 2);
        for s in ["s0", "s_1"] {
            std::fs::remove_file(dir.join("mock").join(format!("cart_{s}.audio_md5.csv.gz"))).unwrap();
        }
        let res = Verify { dir: &dir, family: "mock", short_id: "cart", known: &known, audio: true }.run::<Mock>(&rom()).unwrap();
        let (a0, a1) = (res[0].audio.clone().unwrap(), res[1].audio.clone().unwrap());
        assert_eq!((a0.unit, a0.ok, a0.total), ("segments", 1, 1));
        assert_eq!((a1.ok, a1.total), (1, 1)); // block 0 was left out of the segments
        let _ = std::fs::remove_dir_all(&dir);
    }
}
