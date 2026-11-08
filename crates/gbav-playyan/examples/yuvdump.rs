//! Write the decoded pictures of a file as I420 (displayed size): yuvdump FW MEDIA OUT.yuv [N]
use gbav_playyan::player::{self};
use gbav_playyan::{m4v, Firmware};
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let fw = Firmware::from_paths(&[&a[1]]).unwrap();
    let b = std::fs::read(&a[2]).unwrap();
    let c = player::detect(&b).unwrap();
    let img = player::video_image(&fw, c).unwrap();
    let asf = gbav_playyan::asf::Asf::parse(&b).unwrap();
    let mut d = m4v::Decoder::new(img, &asf.video.as_ref().unwrap().codec_data).unwrap();
    let n: usize = a.get(4).map(|s| s.parse().unwrap()).unwrap_or(usize::MAX);
    let mut out = Vec::new();
    let mut k = 0;
    for o in &asf.video_objects {
        if let m4v::Decoded::Frame(_) = d.decode(&o.data).unwrap() {
            out.extend(d.picture().i420());
            k += 1;
            if k >= n {
                break;
            }
        }
    }
    std::fs::write(&a[3], out).unwrap();
}
