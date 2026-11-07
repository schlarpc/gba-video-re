//! Wall-time benchmark: decode whole streams (video only, then audio only) and print the
//! time per stream, for comparison with the reference C CLI
//! (`fvmvdec ROM --stream S` and `fvmvdec ROM --stream S --no-video --wav /dev/null`).
//!
//! usage: cargo run --release -p gbav-fvmv --example fvmv_bench -- ROM... [--md5]
//! With --md5 it also hashes every frame and block (like `fvmvdec --md5 --audio-md5`).

use gbav_common::md5::digest;
use gbav_fvmv::{Audio, Stream, Video};
use std::hint::black_box;
use std::sync::Arc;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let md5 = args.iter().any(|a| a == "--md5");
    let (mut tv, mut ta) = (0.0, 0.0);
    for path in args.iter().filter(|a| !a.starts_with("--")) {
        let rom = std::fs::read(path).expect("read ROM");
        for s in ["0", "1"] {
            let Ok(st) = Stream::open(&rom, s) else {
                continue;
            };
            let st = Arc::new(st);
            let t0 = Instant::now();
            let mut v = Video::new(st.clone());
            while v.decode_next().expect("decode").is_some() {
                if md5 {
                    let f = v.decoder().last_frame();
                    black_box(digest(f));
                    let mut t = f.to_vec();
                    t.iter_mut().skip(1).step_by(2).for_each(|b| *b &= 0x7f);
                    black_box(digest(&t));
                }
            }
            let dv = t0.elapsed().as_secs_f64();
            let t0 = Instant::now();
            let mut a = Audio::new(st.clone());
            let mut n = 0usize;
            while a.decode_next().is_some() {
                if md5 {
                    black_box(digest(&a.synth_bytes()));
                    black_box(digest(&a.pcm_bytes()));
                }
                black_box(a.pcm());
                n += 1;
            }
            let da = t0.elapsed().as_secs_f64();
            println!(
                "{path} stream {s}: {} frames video {dv:.3} s, {n} blocks audio {da:.3} s",
                st.frames()
            );
            tv += dv;
            ta += da;
        }
    }
    println!("total: video {tv:.3} s, audio {ta:.3} s");
}
