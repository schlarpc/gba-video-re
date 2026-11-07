//! Whole-stream timing, to compare with the C reference:
//!   vx_bench ROM [STREAM] [decode|blit|md5|audio]
//!   decode  decode only                      (C: vxdec ROM --stream S --count 1000000)
//!   blit    decode + BGR555 blit
//!   md5     decode + blit + md5 of slot and picture, one line per frame on stdout like
//!           `vxdec --md5` (index, slot md5, picture md5, start bit)
//!   audio   all AFrames, 8-bit output stage included
use std::io::Write;
use std::time::Instant;

use gbav_vx::util::{md5_hex, md5_hex_u16};
use gbav_vx::{VxAudio, VxVideo};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let rom = std::fs::read(&a[1]).expect("read ROM");
    let stream = a.get(2).map(String::as_str).unwrap_or("0");
    let mode = a.get(3).map(String::as_str).unwrap_or("decode");
    let t0 = Instant::now();
    let n;
    if mode == "audio" {
        let mut au = VxAudio::open(&rom[..], stream).unwrap();
        let mut out = [0u8; 512];
        let mut k = 0u64;
        while au.next_into(&mut out) {
            k += 1;
        }
        n = k;
    } else {
        let mut v = VxVideo::open(&rom[..], stream).unwrap();
        let (w, h) = (v.header().width, v.header().height);
        let mut px = vec![0u16; (w * h) as usize];
        let stdout = std::io::stdout();
        let mut o = std::io::BufWriter::new(stdout.lock());
        let mut k = 0u64;
        while let Some(f) = v.decode_next().unwrap() {
            k += 1;
            match mode {
                "blit" => v.bgr555_into(&mut px),
                "md5" => {
                    v.bgr555_into(&mut px);
                    writeln!(o, "{f} {} {} bit={}", md5_hex(v.slot()), md5_hex_u16(&px), v.frame_bit()).unwrap();
                }
                _ => {}
            }
        }
        n = k;
    }
    let dt = t0.elapsed().as_secs_f64();
    eprintln!("{mode}: {n} units in {dt:.2} s ({:.0}/s)", n as f64 / dt);
}
