//! Compare decoded video frames (ASF or MP4) with raw firmware frame dumps (LLE_FRAMES format:
//! dir/fNNNNN.bin = 240x160 RGB565, dir/index.txt = "n gba_frame addr elapsed_ms").
//! usage: fwcmp FIRMWARE_FILE MEDIA DUMPDIR [brightness]   (env FWCMP_OFF = elapsed - pts, default
//! 100 = original Play-Yan; 0 on the Micro; FWCMP_OUT=dir writes our frames; FWCMP_IMAGE=name)
use gbav_playyan::player::{self, VideoPlayer};
use gbav_playyan::{Firmware, ImageId};
use std::collections::HashMap;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let fw = Firmware::from_paths(&[&a[1]]).unwrap();
    let b = std::fs::read(&a[2]).unwrap();
    let c = player::detect(&b).unwrap();
    let img = match std::env::var("FWCMP_IMAGE") {
        Ok(n) => fw.image(ImageId::ALL.into_iter().find(|i| i.name() == n).unwrap()).unwrap(),
        Err(_) => player::video_image(&fw, c).unwrap(),
    };
    let off: u32 = std::env::var("FWCMP_OFF").ok().and_then(|s| s.parse().ok()).unwrap_or(100);
    let idx = std::fs::read_to_string(format!("{}/index.txt", a[3])).unwrap();
    let mut by_ms: HashMap<u32, usize> = HashMap::new();
    for l in idx.lines() {
        let f: Vec<&str> = l.split_whitespace().collect();
        by_ms.insert(f[3].parse().unwrap(), f[0].parse().unwrap());
    }
    let mut vp = VideoPlayer::open(img, &b).unwrap();
    vp.brightness = a.get(4).map(|s| s.parse().unwrap()).unwrap_or(256);
    let (mut n, mut exact) = (0, 0);
    loop {
        let f = match vp.next_frame() {
            Ok(Some(f)) => f,
            Ok(None) => break,
            Err(e) => {
                println!("stopped: {e}");
                break;
            }
        };
        let Some(&k) = by_ms.get(&(f.pts_ms + off)) else { continue };
        let raw = std::fs::read(format!("{}/f{k:05}.bin", a[3])).unwrap();
        let fwf: Vec<u16> = raw.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        if let Some(d) = std::env::var_os("FWCMP_OUT") {
            let bb: Vec<u8> = f.rgb565.iter().flat_map(|p| p.to_le_bytes()).collect();
            std::fs::write(std::path::Path::new(&d).join(format!("f{k:05}.bin")), bb).unwrap();
        }
        let diff = f.rgb565.iter().zip(&fwf).filter(|(x, y)| x != y).count();
        n += 1;
        if diff == 0 {
            exact += 1;
        } else if n - exact <= 5 {
            println!("frame {} pts {} dump {k}: {diff} pixels differ", f.index, f.pts_ms);
        }
    }
    println!("{} ({}): {exact}/{n} dumped frames bit-exact; {} samples in the container", a[2], img.id.name(), vp.frames_in_container());
}
