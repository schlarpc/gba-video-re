//! Differential fuzz against the reference C decoder.
//!
//! The C sources (`fvmvdec.c`, `fvmvaudio.c` and headers, from the FVMV agent lane) are
//! compiled with `tests/cref/harness.c` into a helper process that the test drives over a
//! pipe. The case generators are a port of the lane's `fuzz.py`:
//!
//! * video, three payload kinds: `random` (random bit / colour / MV streams), `mutate` (a
//!   real frame with 1-5 bit flips in its bit stream) and `real` (a real frame unchanged),
//!   each on random EWRAM with the player's buffer layout (shifted up one buffer, or the
//!   real one, whose top-row accesses leave EWRAM) or random buffer placement, and with or
//!   without readable bytes after the payload. The whole EWRAM, the return code and every
//!   statistics counter must match.
//! * audio: random and real 40-byte blocks through synthesis + output stage with the state
//!   carried across blocks; the 256 int32 samples, 1024 PCM bytes and all state must match.
//! * output stage alone on arbitrary int32 input (full range, so every wraparound path).
//!
//! Needs `GBAV_FVMV_CREF` (directory of the C sources) and a `cc`; real frames need
//! `GBAV_ROMS`. Case counts: `GBAV_FVMV_FUZZ_VIDEO` (default 3000), `GBAV_FVMV_FUZZ_AUDIO`
//! (default 50000), `GBAV_FVMV_FUZZ_SEED`.

mod common;

use common::Rng;
use gbav_fvmv::audio::{AudioState, BLOCK_BYTES};
use gbav_fvmv::util::OutputStage;
use gbav_fvmv::video::{Decoder, Stats, EWRAM_BASE, EWRAM_SIZE};
use gbav_fvmv::Stream;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

struct CRef {
    child: Child,
    w: BufWriter<ChildStdin>,
    r: BufReader<ChildStdout>,
}

impl CRef {
    fn build(cref: &Path) -> CRef {
        // one binary per test thread: the tests run in parallel and must not race on it
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let k = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let out: PathBuf = Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join(format!("fvmv_cref_harness_{}_{k}", std::process::id()));
        let here = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/cref/harness.c");
        let st = Command::new(std::env::var("CC").unwrap_or_else(|_| "cc".into()))
            .args(["-O2", "-std=c99", "-I"])
            .arg(cref)
            .arg("-o")
            .arg(&out)
            .arg(here)
            .arg(cref.join("fvmvdec.c"))
            .arg(cref.join("fvmvaudio.c"))
            .status()
            .expect("run cc");
        assert!(st.success(), "building the C harness failed");
        let mut child = Command::new(&out)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let w = BufWriter::with_capacity(1 << 20, child.stdin.take().unwrap());
        let r = BufReader::with_capacity(1 << 20, child.stdout.take().unwrap());
        CRef { child, w, r }
    }

    fn read(&mut self, n: usize) -> Vec<u8> {
        let mut v = vec![0u8; n];
        self.r.read_exact(&mut v).expect("read from C harness");
        v
    }

    fn i32s(&mut self, n: usize) -> Vec<i32> {
        self.read(4 * n)
            .chunks_exact(4)
            .map(|c| i32::from_le_bytes(c.try_into().unwrap()))
            .collect()
    }

    fn video(
        &mut self,
        out: u32,
        refb: u32,
        vlen: u32,
        data: &[u8],
        mem: &[u8],
    ) -> (i32, Vec<u8>, Vec<u64>) {
        self.w.write_all(b"V").unwrap();
        for x in [out, refb, vlen, data.len() as u32] {
            self.w.write_all(&x.to_le_bytes()).unwrap();
        }
        self.w.write_all(data).unwrap();
        self.w.write_all(mem).unwrap();
        self.w.flush().unwrap();
        let rc = self.i32s(1)[0];
        let ew = self.read(EWRAM_SIZE);
        let st = self
            .read(143 * 8)
            .chunks_exact(8)
            .map(|c| u64::from_le_bytes(c.try_into().unwrap()))
            .collect();
        (rc, ew, st)
    }

    fn audio(&mut self, reset: bool, blk: &[u8]) -> (Vec<i32>, Vec<u8>, Vec<i32>) {
        self.w.write_all(&[b'A', reset as u8]).unwrap();
        self.w.write_all(blk).unwrap();
        self.w.flush().unwrap();
        (self.i32s(256), self.read(1024), self.i32s(13))
    }

    fn output(&mut self, reset: bool, input: &[i32]) -> (Vec<u8>, Vec<i32>) {
        self.w.write_all(&[b'O', reset as u8]).unwrap();
        self.w
            .write_all(&(input.len() as u32).to_le_bytes())
            .unwrap();
        for x in input {
            self.w.write_all(&x.to_le_bytes()).unwrap();
        }
        self.w.flush().unwrap();
        (self.read(4 * input.len()), self.i32s(3))
    }
}

impl Drop for CRef {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn flat_stats(s: &Stats) -> Vec<u64> {
    let mut v: Vec<u64> = s.mode_count.iter().flatten().copied().collect();
    v.extend([
        s.oob_reads,
        s.oob_writes,
        s.offpic_reads,
        s.offpic_writes,
        s.err,
        s.overrun,
        s.mv_xwrap,
        s.mv_yout,
    ]);
    v.extend(s.mv_yout_kind);
    v.extend([s.edge_x0, s.edge_y0, s.inexact]);
    v
}

/// (video payload, audio blocks) of the real frames of every FVMV stream in GBAV_ROMS.
fn real_frames() -> Vec<(Vec<u8>, Vec<u8>)> {
    let Some(dir) = common::roms_dir() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (_, path) in common::fvmv_carts(&dir) {
        let rom = std::fs::read(path).unwrap();
        for s in ["0", "1"] {
            let st = Stream::open(&rom, s).unwrap();
            for i in 0..st.frames() {
                out.push((st.video_payload(i).to_vec(), st.audio_payload(i).to_vec()));
            }
        }
    }
    out
}

fn random_payload(rng: &mut Rng) -> Vec<u8> {
    let nb = rng.range(256, 12000) & !3;
    let nc = rng.range(0, 8000) & !1;
    let nm = rng.range(0, 3000);
    let mut v = Vec::new();
    v.extend_from_slice(&(nb as u32).to_le_bytes());
    v.extend_from_slice(&(nc as u32).to_le_bytes());
    v.extend(rng.bytes((nb + nc + nm) as usize));
    v
}

fn mutate(v: &[u8], rng: &mut Rng) -> Vec<u8> {
    let nb = u32::from_le_bytes(v[..4].try_into().unwrap()) as u64;
    let mut b = v.to_vec();
    for _ in 0..rng.range(1, 6) {
        let k = 8 + rng.range(0, nb.max(1)) as usize;
        if k < b.len() {
            b[k] ^= 1 << rng.range(0, 8);
        }
    }
    b
}

fn cref() -> Option<CRef> {
    match std::env::var_os("GBAV_FVMV_CREF") {
        Some(p) => Some(CRef::build(Path::new(&p))),
        None => {
            eprintln!(
                "skipping: set GBAV_FVMV_CREF to the directory holding fvmvdec.c / fvmvaudio.c"
            );
            None
        }
    }
}

fn seed() -> u64 {
    common::env_count("GBAV_FVMV_FUZZ_SEED", 1) as u64
}

#[test]
fn video_matches_c() {
    let Some(mut c) = cref() else { return };
    let n = common::env_count("GBAV_FVMV_FUZZ_VIDEO", 3000);
    let real = real_frames();
    let mut rng = Rng(seed());
    let (lo, hi) = (
        EWRAM_BASE + 8 * 480 + 2,
        EWRAM_BASE + EWRAM_SIZE as u32 - 0x12c00 - 8 * 480 - 2,
    );
    let mut res = std::collections::BTreeMap::<&str, (usize, usize)>::new();
    let mut modes = std::collections::BTreeSet::new();
    let mut bad_examples = Vec::new();
    for i in 0..n {
        let kind = if real.is_empty() {
            "random"
        } else {
            ["random", "mutate", "real"][i % 3]
        };
        let payload = match kind {
            "random" => random_payload(&mut rng),
            k => {
                let v = &real[rng.range(0, real.len() as u64) as usize].0;
                if k == "mutate" {
                    mutate(v, &mut rng)
                } else {
                    v.clone()
                }
            }
        };
        let r = rng.range(0, 100);
        let (out, refb) = if r < 40 {
            // the player's layout, shifted up one buffer
            if rng.chance(0.5) {
                (0x0201_2c08, 0x0202_5808)
            } else {
                (0x0202_5808, 0x0201_2c08)
            }
        } else if r < 50 {
            // the player's real layout: top-row accesses of buffer 0 leave EWRAM
            let sel = rng.range(0, 2) as u32;
            (Decoder::buffer_addr(sel), Decoder::buffer_addr(sel ^ 1))
        } else {
            (
                (rng.range(lo as u64, hi as u64) as u32) & !3,
                (rng.range(lo as u64, hi as u64) as u32) & !1,
            )
        };
        let mem = rng.bytes(EWRAM_SIZE);
        let mut data = payload.clone();
        if rng.chance(0.8) {
            data.extend(rng.bytes(0x8000));
        }
        let vlen = payload.len() as u32;
        let (rc, ew_c, st_c) = c.video(out, refb, vlen, &data, &mem);
        let mut d = Decoder::new();
        d.ewram_mut().copy_from_slice(&mem);
        let ok = d.decode_frame_at(&data, vlen, out, refb);
        let st_r = flat_stats(&d.stats);
        for z in 0..16 {
            for m in 0..8 {
                if d.stats.mode_count[z][m] != 0 {
                    modes.insert((z, m));
                }
            }
        }
        let same = (rc == 0) == ok && ew_c == d.ewram() && st_c == st_r;
        let e = res.entry(kind).or_default();
        if same {
            e.0 += 1;
        } else {
            e.1 += 1;
            if bad_examples.len() < 5 {
                let first = (0..EWRAM_SIZE).find(|&k| ew_c[k] != d.ewram()[k]);
                bad_examples.push(format!(
                    "case {i} {kind} out={out:08x} ref={refb:08x} rc={rc} ok={ok} first ewram diff {first:?} stats equal {}",
                    st_c == st_r
                ));
            }
        }
    }
    for (k, (g, b)) in &res {
        println!("video {k}: {g} ok, {b} mismatch");
    }
    println!(
        "video coverage: (size, mode) pairs hit: {} of 88",
        modes.len()
    );
    assert!(bad_examples.is_empty(), "{bad_examples:#?}");
    if n >= 300 {
        assert_eq!(modes.len(), 88, "fuzz did not hit every (size, mode) pair");
    }
}

#[test]
fn audio_matches_c() {
    let Some(mut c) = cref() else { return };
    let n = common::env_count("GBAV_FVMV_FUZZ_AUDIO", 50000);
    let mut rng = Rng(seed() ^ 0xa0d10);
    let real: Vec<Vec<u8>> = real_frames()
        .into_iter()
        .take(4000)
        .flat_map(|(_, a)| {
            a.chunks_exact(BLOCK_BYTES)
                .map(<[u8]>::to_vec)
                .collect::<Vec<_>>()
        })
        .collect();
    let random: Vec<Vec<u8>> = (0..n).map(|_| rng.bytes(BLOCK_BYTES)).collect();
    for (name, blocks) in [("random", &random), ("real", &real)] {
        let mut st = AudioState::new();
        let mut bad = 0;
        for (i, blk) in blocks.iter().enumerate() {
            let (syn_c, pcm_c, state_c) = c.audio(i == 0, blk);
            let mut syn = [0i32; 256];
            let mut pcm = [0i8; 1024];
            st.decode_block(blk.as_slice().try_into().unwrap(), &mut syn, &mut pcm);
            let mut state_r = st.synth.v.to_vec();
            state_r.extend([st.synth.msr, st.output.err, st.output.fb, st.output.prev]);
            let pcm_r: Vec<u8> = pcm.iter().map(|&b| b as u8).collect();
            if syn_c != syn || pcm_c != pcm_r || state_c != state_r {
                bad += 1;
                if bad < 5 {
                    println!(
                        "audio {name} block {i}: synth {} pcm {} state {}",
                        syn_c == syn,
                        pcm_c == pcm_r,
                        state_c == state_r
                    );
                }
            }
        }
        println!("audio {name}: {} ok, {bad} mismatch", blocks.len() - bad);
        assert_eq!(bad, 0);
    }
}

#[test]
fn output_stage_matches_c() {
    let Some(mut c) = cref() else { return };
    let mut rng = Rng(seed() ^ 0x0757);
    let mut st = OutputStage::new();
    let mut bad = 0;
    let runs = 2000;
    for i in 0..runs {
        let n = rng.range(1, 300) as usize;
        // mix full-range values (wraparound in 3*p + x, the adds and the error terms)
        // with values near the 8-bit range edges
        let input: Vec<i32> = (0..n)
            .map(|_| match rng.range(0, 3) {
                0 => rng.next_u64() as i32,
                1 => (rng.next_u64() as i32) >> 12,
                _ => (rng.range(0, 1 << 17) as i32) - (1 << 16),
            })
            .collect();
        let reset = i % 50 == 0;
        if reset {
            st = OutputStage::new();
        }
        let (out_c, state_c) = c.output(reset, &input);
        let mut out = vec![0i8; 4 * n];
        st.process(&input, &mut out);
        let out_r: Vec<u8> = out.iter().map(|&b| b as u8).collect();
        if out_c != out_r || state_c != [st.err, st.fb, st.prev] {
            bad += 1;
        }
    }
    println!("output stage: {} ok, {bad} mismatch", runs - bad);
    assert_eq!(bad, 0);
}
