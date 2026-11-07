//! Small helpers (candidates for gbav-common).

/// Little-endian u32 at `o`; bytes past the end read as zero.
#[inline]
pub fn rd32(b: &[u8], o: usize) -> u32 {
    let g = |i: usize| b.get(o.wrapping_add(i)).copied().unwrap_or(0) as u32;
    g(0) | g(1) << 8 | g(2) << 16 | g(3) << 24
}

/// Little-endian u16 at `o`; bytes past the end read as zero.
#[inline]
pub fn rd16(b: &[u8], o: usize) -> u16 {
    let g = |i: usize| b.get(o.wrapping_add(i)).copied().unwrap_or(0) as u16;
    g(0) | g(1) << 8
}

/// Decode a name in the players' substitution alphabet: 1..10 digits, 11..36 a-z, 37..62 A-Z,
/// 0xFF space, 0xFE newline, and the punctuation codes seen in titles and settings.
pub fn deobfuscate(b: &[u8]) -> String {
    b.iter()
        .map(|&c| match c {
            1..=10 => (b'0' + c - 1) as char,
            11..=36 => (b'a' + c - 11) as char,
            37..=62 => (b'A' + c - 37) as char,
            0xff => ' ',
            0xfe => '\n',
            0x3f => '.',
            0x40 => ',',
            0x41 => ':',
            0x45 => '&',
            0x48 => '\'',
            0x49 => '-',
            0x4a => '/',
            0x4b => '+',
            0x4c => ' ',
            0x4e => '_',
            _ => '?',
        })
        .collect()
}

/// A cheap, owning view of a byte range (decoders copy the part of the ROM they need once and
/// hand out ranges of it to per-segment decompressors).
#[derive(Clone, Debug)]
pub struct ArcSlice {
    data: std::sync::Arc<[u8]>,
    start: usize,
    end: usize,
}

impl ArcSlice {
    pub fn new(data: std::sync::Arc<[u8]>) -> ArcSlice {
        let end = data.len();
        ArcSlice { data, start: 0, end }
    }
    /// Sub-range `start..end` of this view (clamped to it).
    pub fn slice(&self, start: usize, end: usize) -> ArcSlice {
        let s = (self.start + start).min(self.end);
        let e = (self.start + end).min(self.end).max(s);
        ArcSlice { data: self.data.clone(), start: s, end: e }
    }
}

impl AsRef<[u8]> for ArcSlice {
    fn as_ref(&self) -> &[u8] {
        &self.data[self.start..self.end]
    }
}
