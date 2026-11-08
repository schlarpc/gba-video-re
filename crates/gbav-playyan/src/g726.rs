//! G.726 ADPCM (16, 24, 32, 40 kbit/s), as the Play-Yan video firmware decodes the ASF audio.
//!
//! The firmware (py_fw0 0x3e6 / 0x5ae / 0x7d6 / 0xa56, predictor 0xb5c, update 0xc80,
//! quantiser 0xebe) has the structure of Sun's public G.72x reference code (g72x.c, g721.c,
//! g723_24.c, g723_40.c), including its 16-bit `short` intermediates, and the ITU-T G.726
//! 16 kbit/s tables, with one deviation on the decoder side: the linear output `sr << 2` is
//! clamped to 16 bits (Sun's code lets it wrap).
//! All tables here are the published ITU-T G.726 values.
//!
//! Packing (0x1234e): the codes of an audio payload are read MSB first: 32 kbit/s takes the high
//! nibble, then the low one; 16 kbit/s bits 7-6, 5-4, 3-2, 1-0; 24 and 40 kbit/s take 3- and
//! 5-bit groups from big-endian 24- and 40-bit words. Each decoded sample is written to both
//! halves of a stereo word.

const POWER2: [i32; 15] = [1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000];

fn quan(val: i32, table: &[i32]) -> usize {
    table.iter().position(|&t| val < t).unwrap_or(table.len())
}

fn fmult(an: i32, srn: i32) -> i32 {
    let anmag = if an > 0 { an } else { (-an) & 0x1FFF };
    let anexp = quan(anmag, &POWER2) as i32 - 6;
    let anmant = if anmag == 0 {
        32
    } else if anexp >= 0 {
        anmag >> anexp
    } else {
        anmag << -anexp
    };
    let wanexp = anexp + ((srn >> 6) & 0xF) - 13;
    let wanmant = (anmant * (srn & 0o77) + 0x30) >> 4;
    let retval = if wanexp >= 0 { (wanmant << wanexp) & 0x7FFF } else { wanmant >> -wanexp };
    if (an ^ srn) < 0 {
        -retval
    } else {
        retval
    }
}

#[inline]
fn s16(v: i32) -> i32 {
    v as i16 as i32
}

/// Per-rate tables (dqln, wi, fi), as in the ITU-T G.726 recommendation / Sun's code.
struct Rate {
    bits: u32,
    dqln: &'static [i32],
    wi: &'static [i32],
    fi: &'static [i32],
    /// Sun's g723_40.c masks dq with 0x7FFF when forming sr; the others with 0x3FFF.
    srmask: i32,
}

const R16: Rate = Rate {
    bits: 2,
    dqln: &[116, 365, 365, 116],
    wi: &[-704, 14048, 14048, -704],
    fi: &[0, 0xE00, 0xE00, 0],
    srmask: 0x3FFF,
};
const R24: Rate = Rate {
    bits: 3,
    dqln: &[-2048, 135, 273, 373, 373, 273, 135, -2048],
    wi: &[-128, 960, 4384, 18624, 18624, 4384, 960, -128],
    fi: &[0, 0x200, 0x400, 0xE00, 0xE00, 0x400, 0x200, 0],
    srmask: 0x3FFF,
};
const R32: Rate = Rate {
    bits: 4,
    dqln: &[-2048, 4, 135, 213, 273, 323, 373, 425, 425, 373, 323, 273, 213, 135, 4, -2048],
    wi: &[-384, 576, 1312, 2048, 3584, 6336, 11360, 35904, 35904, 11360, 6336, 3584, 2048, 1312, 576, -384],
    fi: &[0, 0, 0, 0x200, 0x200, 0x200, 0x600, 0xE00, 0xE00, 0x600, 0x200, 0x200, 0x200, 0, 0, 0],
    srmask: 0x3FFF,
};
const R40: Rate = Rate {
    bits: 5,
    dqln: &[
        -2048, -66, 28, 104, 169, 224, 274, 318, 358, 395, 429, 459, 488, 514, 539, 566, 566, 539, 514, 488, 459, 429, 395,
        358, 318, 274, 224, 169, 104, 28, -66, -2048,
    ],
    wi: &[
        448, 448, 768, 1248, 1280, 1312, 1856, 3200, 4512, 5728, 7008, 8960, 11456, 14080, 16928, 22272, 22272, 16928, 14080,
        11456, 8960, 7008, 5728, 4512, 3200, 1856, 1312, 1280, 1248, 768, 448, 448,
    ],
    fi: &[
        0, 0, 0, 0, 0, 0x200, 0x200, 0x200, 0x200, 0x200, 0x400, 0x600, 0x800, 0xA00, 0xC00, 0xC00, 0xC00, 0xC00, 0xA00,
        0x800, 0x600, 0x400, 0x200, 0x200, 0x200, 0x200, 0x200, 0, 0, 0, 0, 0,
    ],
    srmask: 0x7FFF,
};

/// Decoder state (Sun's `struct g72x_state`, short fields kept 16-bit).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct State {
    yl: i32,
    yu: i32,
    dms: i32,
    dml: i32,
    ap: i32,
    a: [i32; 2],
    b: [i32; 6],
    pk: [i32; 2],
    dq: [i32; 6],
    sr: [i32; 2],
    td: i32,
}

impl Default for State {
    /// The firmware's state initialiser 0x122e4 (= Sun's g72x_init_state).
    fn default() -> Self {
        State { yl: 34816, yu: 544, dms: 0, dml: 0, ap: 0, a: [0; 2], b: [0; 6], pk: [0; 2], dq: [32; 6], sr: [32; 2], td: 0 }
    }
}

fn float_of(v: i32) -> i32 {
    let exp = quan(v, &POWER2) as i32;
    (exp << 6) + ((v << 6) >> exp)
}

impl State {
    fn predictor_zero(&self) -> i32 {
        (0..6).map(|i| fmult(self.b[i] >> 2, self.dq[i])).sum()
    }
    fn predictor_pole(&self) -> i32 {
        fmult(self.a[1] >> 2, self.sr[1]) + fmult(self.a[0] >> 2, self.sr[0])
    }
    fn step_size(&self) -> i32 {
        if self.ap >= 256 {
            return self.yu;
        }
        let mut y = self.yl >> 6;
        let dif = self.yu - y;
        let al = self.ap >> 2;
        if dif > 0 {
            y += (dif * al) >> 6;
        } else if dif < 0 {
            y += (dif * al + 0x3F) >> 6;
        }
        y
    }

    #[allow(clippy::too_many_arguments)]
    fn update(&mut self, code_size: u32, y: i32, wi: i32, fi: i32, dq: i32, sr: i32, dqsr: i32) {
        let pk0 = (dqsr < 0) as i32;
        let mag = dq & 0x7FFF;
        let ylint = self.yl >> 15;
        let ylfrac = (self.yl >> 10) & 0x1F;
        let thr1 = (32 + ylfrac) << ylint;
        let thr2 = if ylint > 9 { 31 << 10 } else { thr1 };
        let dqthr = (thr2 + (thr2 >> 1)) >> 1;
        let tr = self.td != 0 && mag > dqthr;
        self.yu = s16(y + ((wi - y) >> 5)).clamp(544, 5120);
        self.yl += self.yu + ((-self.yl) >> 6);
        let a2p;
        if tr {
            self.a = [0; 2];
            self.b = [0; 6];
            a2p = 0;
        } else {
            let pks1 = pk0 ^ self.pk[0];
            let mut a2 = self.a[1] - (self.a[1] >> 7);
            if dqsr != 0 {
                let fa1 = if pks1 != 0 { self.a[0] } else { -self.a[0] };
                if fa1 < -8191 {
                    a2 -= 0x100;
                } else if fa1 > 8191 {
                    a2 += 0xFF;
                } else {
                    a2 += fa1 >> 5;
                }
                if pk0 ^ self.pk[1] != 0 {
                    if a2 <= -12160 {
                        a2 = -12288;
                    } else if a2 >= 12416 {
                        a2 = 12288;
                    } else {
                        a2 -= 0x80;
                    }
                } else if a2 <= -12416 {
                    a2 = -12288;
                } else if a2 >= 12160 {
                    a2 = 12288;
                } else {
                    a2 += 0x80;
                }
            }
            a2p = a2;
            self.a[1] = s16(a2p);
            let mut a0 = self.a[0] - (self.a[0] >> 8);
            if dqsr != 0 {
                a0 = if pks1 == 0 { a0 + 192 } else { a0 - 192 };
            }
            let a1ul = 15360 - a2p;
            a0 = a0.clamp(-a1ul, a1ul);
            self.a[0] = s16(a0);
            for c in 0..6 {
                let mut b = self.b[c];
                b -= if code_size == 5 { b >> 9 } else { b >> 8 };
                if dq & 0x7FFF != 0 {
                    b = if (dq ^ self.dq[c]) >= 0 { b + 128 } else { b - 128 };
                }
                self.b[c] = s16(b);
            }
        }
        for c in (1..6).rev() {
            self.dq[c] = self.dq[c - 1];
        }
        self.dq[0] = if mag == 0 {
            if dq >= 0 {
                0x20
            } else {
                s16(0xFC20)
            }
        } else if dq >= 0 {
            s16(float_of(mag))
        } else {
            s16(float_of(mag) - 0x400)
        };
        self.sr[1] = self.sr[0];
        self.sr[0] = if sr == 0 {
            0x20
        } else if sr > 0 {
            s16(float_of(sr))
        } else if sr > -32768 {
            s16(float_of(-sr) - 0x400)
        } else {
            s16(0xFC20)
        };
        self.pk[1] = self.pk[0];
        self.pk[0] = pk0;
        self.td = if tr {
            0
        } else {
            (a2p < -11776) as i32
        };
        self.dms = s16(self.dms + ((fi - self.dms) >> 5));
        self.dml = s16(self.dml + (((fi << 2) - self.dml) >> 7));
        if tr {
            self.ap = 256;
        } else if y < 1536 || self.td == 1 || ((self.dms << 2) - self.dml).abs() >= (self.dml >> 3) {
            self.ap += (0x200 - self.ap) >> 4;
        } else {
            self.ap += (-self.ap) >> 4;
        }
        self.ap = s16(self.ap);
    }

    fn reconstruct(sign: bool, dqln: i32, y: i32) -> i32 {
        let dql = dqln + (y >> 2);
        if dql < 0 {
            return if sign { -0x8000 } else { 0 };
        }
        let dex = (dql >> 7) & 15;
        let dqt = 128 + (dql & 127);
        let dq = (dqt << 7) >> (14 - dex);
        if sign {
            dq - 0x8000
        } else {
            dq
        }
    }

    /// One decoder step with the `short` variables of Sun's code (sezi, sei, se, y, dq, sr,
    /// dqsr are 16-bit; the firmware sign-extends each, 0x3e6.. `exts16`), then the firmware's
    /// output clamp.
    fn decode_with(&mut self, r: &Rate, code: u32) -> i16 {
        let i = (code & ((1 << r.bits) - 1)) as usize;
        let sezi = s16(self.predictor_zero());
        let sez = sezi >> 1;
        let sei = s16(sezi + self.predictor_pole());
        let se = sei >> 1;
        let y = s16(self.step_size());
        let dq = s16(Self::reconstruct(i & (1 << (r.bits - 1)) != 0, r.dqln[i], y));
        let sr = s16(if dq < 0 { se - (dq & r.srmask) } else { se + dq });
        let dqsr = s16(sr - se + sez);
        self.update(r.bits, y, r.wi[i], r.fi[i], dq, sr, dqsr);
        (sr << 2).clamp(-32768, 32767) as i16
    }
}

/// A G.726 decoder for one ASF audio stream.
#[derive(Clone, Debug)]
pub struct Decoder {
    bits: u32,
    state: State,
}

impl Decoder {
    /// `bits` = 2, 3, 4 or 5 (16, 24, 32, 40 kbit/s at 8 kHz).
    pub fn new(bits: u32) -> Option<Decoder> {
        (2..=5).contains(&bits).then(|| Decoder { bits, state: State::default() })
    }
    pub fn bits(&self) -> u32 {
        self.bits
    }
    fn rate(&self) -> &'static Rate {
        match self.bits {
            2 => &R16,
            3 => &R24,
            4 => &R32,
            _ => &R40,
        }
    }
    /// Decode one code.
    pub fn decode_code(&mut self, code: u32) -> i16 {
        let r = self.rate();
        self.state.decode_with(r, code)
    }
    /// Decode a packed payload (MSB-first codes) into 16-bit linear PCM, appended to `out`.
    /// Trailing bits that do not make a whole code are ignored.
    pub fn decode_payload(&mut self, data: &[u8], out: &mut Vec<i16>) {
        let n = data.len() * 8 / self.bits as usize;
        let mut br = crate::bits::BitReader::new(data);
        for _ in 0..n {
            let c = br.get(self.bits);
            out.push(self.decode_code(c));
        }
    }
    pub fn state(&self) -> &State {
        &self.state
    }
}
