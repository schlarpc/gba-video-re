//! YUV -> BGR555 blit (IWRAM 0x03000370), brightness 0.
//!
//! For pixel (x,y), Y = luma, U,V = the chroma pair (x>>1, y>>1):
//!   Yd = Y - 4*((x+y) & 1)                     2x2 ordered dither on the luma index
//!   R = T[Yd + 2(V-128)]; G = T[Yd - ((V-128) + ((U-128) >> 1))]; B = T[Yd + 2(U-128)]
//! T = the runtime IWRAM bytes at 0x030001f0 + k, k in -260..509: a 5-bit clamp table for
//! k in 0..~255 that runs into the IRQ code before it and into the blitter's own code after
//! it, including the two immediates player_run patches for the stream size (0x38c
//! `mov sl,#height`, 0x394 `mov sl,#width`). The blitter adds the six table values for a
//! pixel pair into one register (R | G<<5 | B<<10, the odd pixel << 16) and stores a word, so
//! table bytes > 31 carry into the next field and into the odd pixel; modelled exactly.
//! The A/B brightness patch (shifts T's base by +-0x18/0x30 and changes IWRAM 0x388) is not
//! modelled: brightness stays 0, as in every capture.

const T0: i32 = 0x1f0;

/// The patched runtime IWRAM bytes 0x03000000..0x030003ff for one picture size.
#[derive(Clone)]
pub struct BlitLut {
    iw: [u8; 0x400],
}

/// ARM `mov sl,#v` as player_run encodes it (0x03006df4): rotated 8-bit immediate.
fn mov_sl_imm(mut v: u32) -> u32 {
    let mut rot: u32 = 0x10;
    while v > 0xff && v & 3 == 0 {
        v >>= 2;
        rot -= 1;
    }
    0xe3a0_a000 | v | (rot & 0xf) << 8
}

impl BlitLut {
    /// `iwram_lo`: runtime IWRAM 0x03000000..0x030003ff with the IRQ entries installed.
    pub fn new(iwram_lo: &[u8; 0x400], width: u32, height: u32) -> BlitLut {
        let mut iw = *iwram_lo;
        iw[0x38c..0x390].copy_from_slice(&mov_sl_imm(height).to_le_bytes());
        iw[0x394..0x398].copy_from_slice(&mov_sl_imm(width).to_le_bytes());
        BlitLut { iw }
    }

    #[inline(always)]
    fn t(&self, k: i32) -> u32 {
        self.iw[(T0 + k) as usize] as u32
    }

    /// Convert a 0xF000-byte decoder slot into `width * height` BGR555 words, row-major.
    pub fn blit(&self, slot: &[u8], out: &mut [u16], width: u32, height: u32) {
        let (w, h) = (width as usize, height as usize);
        assert!(slot.len() >= 0xF000 && out.len() >= w * h);
        for y in 0..h {
            let yrow = &slot[y * 256..y * 256 + w];
            let crow = &slot[0xA000 + (y >> 1) * 256..0xA000 + (y >> 1) * 256 + w];
            let orow = &mut out[y * w..y * w + w];
            for x in (0..w).step_by(2) {
                let u = crow[x] as i32 - 128;
                let v = crow[x + 1] as i32 - 128;
                let (rv, gv, bv) = (2 * v, -(v + (u >> 1)), 2 * u);
                let mut word: u32 = 0;
                for k in 0..2 {
                    let yy = yrow[x + k] as i32 - 4 * ((x + k + y) as i32 & 1);
                    let px = self.t(yy + rv) + (self.t(yy + gv) << 5) + (self.t(yy + bv) << 10);
                    word = word.wrapping_add(px << (16 * k));
                }
                orow[x] = word as u16;
                orow[x + 1] = (word >> 16) as u16;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn mov_imm() {
        // 240 = 0xf0 fits; 160 = 0xa0 fits: rotation field 0 after the & 0xf mask
        assert_eq!(super::mov_sl_imm(240), 0xe3a0_a0f0);
        assert_eq!(super::mov_sl_imm(160), 0xe3a0_a0a0);
        assert_eq!(super::mov_sl_imm(112), 0xe3a0_a070);
    }
}
