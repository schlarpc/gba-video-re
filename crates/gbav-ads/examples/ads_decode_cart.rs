//! Decode every stream of an ADS cart (video, then audio) and report the wall time.
//!   cargo run --release -p gbav-ads --example ads_decode_cart -- ROM [--video-only|--audio-only] [--stream ID]
use gbav_common::{AudioDecoder, Codec, VideoDecoder};
use gbav_ads::Ads;
use std::time::Instant;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let rom: gbav_common::Rom = std::fs::read(&a[1]).expect("read ROM").into();
    let (video, audio) = (!a.iter().any(|x| x == "--audio-only"), !a.iter().any(|x| x == "--video-only"));
    let only = a.iter().position(|x| x == "--stream").map(|i| a[i + 1].clone());
    let t0 = Instant::now();
    let (mut frames, mut samples) = (0u64, 0u64);
    for s in Ads::streams(&rom).expect("ADS cart") {
        if only.as_ref().is_some_and(|o| *o != s.id) {
            continue;
        }
        if video {
            let mut v = Ads::video(&rom, &s.id).unwrap();
            while v.next_frame().unwrap().is_some() {
                frames += 1;
            }
        }
        if audio && s.audio_rate_hz.is_some() {
            let mut d = Ads::audio(&rom, &s.id).unwrap();
            while d.next_block().unwrap() {
                samples += d.pcm().len() as u64;
            }
        }
    }
    let dt = t0.elapsed().as_secs_f64();
    println!("{frames} frames, {samples} samples in {dt:.3} s ({:.0} fps)", frames as f64 / dt);
}
