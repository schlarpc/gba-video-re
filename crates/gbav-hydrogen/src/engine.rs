//! What the decoder needs to know about the Hydrogen player build, all read from the ROM:
//! the dither table, the brightness gains, the ADPCM output stage, the line-159 behaviour of the
//! blit and the gain bias some boot code sets.

use gbav_common::{Error, Result};
use gbav_majesco::adpcm::Output;
use gbav_majesco::rd32;
use gbav_majesco::vq::{self, Row159, Tables};

/// The Hydrogen player as far as decoding is concerned.
#[derive(Debug, Clone)]
pub struct Player {
    /// ROM offset of the 16-byte ordered-dither table
    pub dither_off: usize,
    pub dither: [u8; 16],
    /// brightness gains (11 levels 128..384 shipped, 8 levels 230..409 in the Sonic X Vol 2 proto)
    pub gains: Vec<i32>,
    /// the level the player starts at (gain 256)
    pub default_level: usize,
    /// ADPCM sample store: clamp (shipped) or wrap (Sonic X Vol 2 proto)
    pub output: Output,
    /// line 159 in the 3-row-block modes: BG palette fill (shipped) or untouched (proto)
    pub row159: Row159,
    /// gain bias the boot code stores after the logo (Jimmy Neutron: 51), 0 if none
    pub bias_after_logo: i32,
}

impl Player {
    /// Read the player's tables from a Hydrogen-engine ROM (code and rodata are in the first
    /// 512 KiB, the IWRAM image at the end).
    pub fn detect(rom: &[u8]) -> Result<Player> {
        let dither_off = vq::find_dither(rom, 0x80000)
            .ok_or_else(|| Error::Format("Hydrogen player dither table not found".into()))?;
        let mut dither = [0u8; 16];
        dither.copy_from_slice(&rom[dither_off..dither_off + 16]);
        let (gains, default_level) = vq::gains_before(rom, dither_off)
            .ok_or_else(|| Error::Format("Hydrogen player brightness table not found".into()))?;
        let output = adpcm_output_stage(rom);
        // The prototype build is the only one without the ADPCM clamp, and also the only one whose
        // 3x3/4x3 blit leaves line 159 alone (sonicx2-proto/NOTES: both differences are against
        // all five shipped builds compared).
        let row159 = if output == Output::Wrap { Row159::Untouched } else { Row159::BLACK };
        let bias_after_logo = boot_gain_bias(rom, dither_off);
        Ok(Player { dither_off, dither, gains, default_level, output, row159, bias_after_logo })
    }

    /// Conversion tables at the default brightness plus `bias`.
    pub fn tables(&self, bias: i32) -> Tables {
        Tables::new(self.dither, self.gains[self.default_level], bias)
    }
}

/// The ADPCM decode loop divides by 64 toward zero (`add r4, sl, fp, lsr #26; asr sl, r4, #6`,
/// identical in every Majesco build) and then clamps (`cmp sl, #0x7f` ...) before the `strb`;
/// the Sonic X Vol 2 prototype stores directly. Default: clamp.
pub fn adpcm_output_stage(rom: &[u8]) -> Output {
    const DIV64: [u8; 8] = [0x2b, 0x4d, 0x8a, 0xe0, 0x44, 0xa3, 0xa0, 0xe1];
    const CMP7F: [u8; 4] = [0x7f, 0x00, 0x5a, 0xe3];
    let mut o = 0;
    while o + 12 <= rom.len() {
        if rom[o..o + 8] == DIV64 {
            return if rom[o + 8..o + 12] == CMP7F { Output::Clip } else { Output::Wrap };
        }
        o += 4;
    }
    Output::Clip
}

/// Boot code that stores a constant to the converter's gain-bias global. The global is one of the
/// IWRAM literals near the reference to the dither table (the brightness-table builder's literal
/// pool); the store is Thumb `ldr rX, =global; movs rY, #K; str rY, [rX]` with K != 0. Found only
/// in Jimmy Neutron (K = 51, executed after the logo); other carts keep the bias at 0.
pub fn boot_gain_bias(rom: &[u8], dither_off: usize) -> i32 {
    let lim = rom.len().min(0x80000) & !3;
    let target = 0x0800_0000u32 + dither_off as u32;
    let mut globals = Vec::new();
    for r in (0..lim).step_by(4) {
        if rd32(rom, r) != target {
            continue;
        }
        let lo = r.saturating_sub(0x200);
        let hi = (r + 0x200).min(lim);
        for q in (lo..hi).step_by(4) {
            let v = rd32(rom, q);
            if (0x0300_0000..0x0300_8000).contains(&v) {
                globals.push(v);
            }
        }
    }
    if globals.is_empty() {
        return 0;
    }
    let code = rom.len().min(0x40000);
    let h = |p: usize| gbav_majesco::rd16(rom, p);
    let mut p = 0;
    while p + 6 <= code {
        let (h0, h1, h2) = (h(p), h(p + 2), h(p + 4));
        // ldr rx, [pc, #imm8*4]; movs ry, #K; str ry, [rx, #0]
        if h0 >> 11 == 0b01001 && h1 >> 11 == 0b00100 && h2 >> 11 == 0b01100 {
            let (rx, ry, k) = ((h0 >> 8) & 7, (h1 >> 8) & 7, (h1 & 0xff) as i32);
            if h2 & 0x7c0 == 0 && h2 & 7 == ry && (h2 >> 3) & 7 == rx && k != 0 {
                let lit = ((p + 4) & !3) + (h0 & 0xff) as usize * 4;
                if globals.contains(&rd32(rom, lit)) {
                    return k;
                }
            }
        }
        p += 2;
    }
    0
}
