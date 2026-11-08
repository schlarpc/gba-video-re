//! fitdump FW W H IN OUT.bin [DW DH]: the firmware fit of one picture -> RGB565 LE. IN is the
//! decoder's buffer layout: Y (pitch and rows MB-aligned), then Cb, Cr at half pitch.
use gbav_playyan::{m4v::Picture, pixel, Firmware};
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let fw = Firmware::from_paths(&[&a[1]]).unwrap();
    let img = fw.video().unwrap();
    let (w, h): (usize, usize) = (a[2].parse().unwrap(), a[3].parse().unwrap());
    let (dw, dh): (usize, usize) = (a.get(6).map_or(240, |s| s.parse().unwrap()), a.get(7).map_or(160, |s| s.parse().unwrap()));
    let d = std::fs::read(&a[4]).unwrap();
    let (stride, rows) = ((w + 15) & !15, (h + 15) & !15);
    let n = stride * rows;
    let p = Picture { width: w as u32, height: h as u32, stride, rows, y: d[..n].to_vec(), cb: d[n..n * 5 / 4].to_vec(), cr: d[n * 5 / 4..n * 3 / 2].to_vec() };
    let mut f = pixel::Fitter::new(&pixel::ThumbTables::from_firmware(img).unwrap());
    let out = f.fit(&p, dw, dh).unwrap();
    std::fs::write(&a[5], out.iter().flat_map(|x| x.to_le_bytes()).collect::<Vec<u8>>()).unwrap();
}
