//! Compare our 60x40 thumbnail (RGB565, as the firmware returns it) with a raw firmware dump.
//! usage: thumbcmp FIRMWARE_FILE THUMB.bin MEDIA.asf...
use gbav_playyan::{asf::Asf, m4v, pixel, Firmware};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let fw = Firmware::from_paths(&[&a[1]]).unwrap();
    let img = fw.video().unwrap();
    let tt = pixel::ThumbTables::from_firmware(img).unwrap();
    for f in &a[3..] {
        let b = std::fs::read(f).unwrap();
        let asf = Asf::parse(&b).unwrap();
        let mut dec = m4v::Decoder::new(img, &asf.video.as_ref().unwrap().codec_data).unwrap();
        let mut px = None;
        for o in &asf.video_objects {
            if let m4v::Decoded::Frame(_) = dec.decode(&o.data).unwrap() {
                px = Some(pixel::thumbnail_rgb565(dec.picture(), &tt).unwrap());
                break;
            }
        }
        let px = px.unwrap();
        let name = std::path::Path::new(f).file_name().unwrap().to_string_lossy().to_string();
        let dump = format!("{}/{}.bin", a[2], name);
        match std::fs::read(&dump) {
            Ok(raw) => {
                let fwp: Vec<u16> = raw.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
                let d = px.iter().zip(&fwp).filter(|(x, y)| x != y).count();
                println!("{name}: {} ({} of 2400 pixels differ)", if d == 0 { "bit-exact" } else { "DIFFERS" }, d);
            }
            Err(_) => println!("{name}: no dump"),
        }
    }
}
