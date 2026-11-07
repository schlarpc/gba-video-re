//! Every ROM in GBAV_ROMS is claimed by at most one of the two Majesco codecs: the 13 Hydrogen
//! carts and the Sonic X Vol 2 proto by `Hydrogen`, the 19 ADS carts and the two Hydrogen-packaged
//! LZMA carts by `Ads`, nothing else by either.

#[path = "../../gbav-majesco/tests/support/mod.rs"]
#[macro_use]
mod support;

use gbav_ads::Ads;
use gbav_common::Codec;
use gbav_hydrogen::Hydrogen;

#[test]
fn each_cart_has_one_majesco_codec() {
    let Some(dir) = support::roms_dir() else { return eprintln!("skip: GBAV_ROMS not set") };
    let mut paths: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().path()).filter(|p| p.extension().is_some_and(|e| e == "gba")).collect();
    paths.sort();
    let res = support::par_map(&paths, |p| {
        let rom = std::fs::read(p).unwrap();
        let h = Hydrogen::streams(&rom).ok().map(|s| s.len());
        let a = Ads::streams(&rom).ok().map(|s| s.len());
        (p.file_stem().unwrap().to_string_lossy().replace("Game Boy Advance Video - ", ""), h, a)
    });
    let (mut nh, mut na) = (0, 0);
    for (n, h, a) in &res {
        eprintln!("{:8} {:8} {n}", h.map_or("-".into(), |k| format!("hy {k}")), a.map_or("-".into(), |k| format!("ads {k}")));
        assert!(h.is_none() || a.is_none(), "{n} claimed by both");
        nh += h.is_some() as usize;
        na += a.is_some() as usize;
    }
    eprintln!("Hydrogen {nh} carts, ADS {na} carts");
    assert_eq!((nh, na), (14, 21));
}
