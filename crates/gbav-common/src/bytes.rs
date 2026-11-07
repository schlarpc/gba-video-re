//! Byte-level helpers shared by the codec crates.

use std::sync::Arc;

/// Little-endian u16 at `off`, or `None` past the end.
#[inline]
pub fn le16(b: &[u8], off: usize) -> Option<u16> {
    b.get(off..off.checked_add(2)?).map(|s| u16::from_le_bytes([s[0], s[1]]))
}

/// Little-endian u32 at `off`, or `None` past the end.
#[inline]
pub fn le32(b: &[u8], off: usize) -> Option<u32> {
    b.get(off..off.checked_add(4)?).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

/// Little-endian u16 at `o`; bytes past the end read as zero (the players' open-bus-free view).
#[inline]
pub fn rd16(b: &[u8], o: usize) -> u16 {
    let g = |i: usize| b.get(o.wrapping_add(i)).copied().unwrap_or(0) as u16;
    g(0) | g(1) << 8
}

/// Little-endian u32 at `o`; bytes past the end read as zero.
#[inline]
pub fn rd32(b: &[u8], o: usize) -> u32 {
    let g = |i: usize| b.get(o.wrapping_add(i)).copied().unwrap_or(0) as u32;
    g(0) | g(1) << 8 | g(2) << 16 | g(3) << 24
}

/// Decode a name in the Majesco players' substitution alphabet: 1..10 digits, 11..36 a-z,
/// 37..62 A-Z, 0xFF space, 0xFE newline, and the punctuation codes seen in titles and settings.
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

/// A cheap, owning view of a byte range of a shared buffer (usually the ROM).
#[derive(Clone, Debug)]
pub struct ArcSlice {
    data: Arc<[u8]>,
    start: usize,
    end: usize,
}

impl ArcSlice {
    pub fn new(data: Arc<[u8]>) -> ArcSlice {
        let end = data.len();
        ArcSlice { data, start: 0, end }
    }
    /// View of `data[start..end]`, clamped to the buffer.
    pub fn range(data: Arc<[u8]>, start: usize, end: usize) -> ArcSlice {
        ArcSlice::new(data).slice(start, end)
    }
    /// Sub-range `start..end` of this view (clamped to it).
    pub fn slice(&self, start: usize, end: usize) -> ArcSlice {
        let s = (self.start + start).min(self.end);
        let e = (self.start + end).min(self.end).max(s);
        ArcSlice { data: self.data.clone(), start: s, end: e }
    }
    /// Offset of this view in the shared buffer.
    pub fn offset(&self) -> usize {
        self.start
    }
    pub fn len(&self) -> usize {
        self.end - self.start
    }
    pub fn is_empty(&self) -> bool {
        self.end == self.start
    }
}

impl AsRef<[u8]> for ArcSlice {
    fn as_ref(&self) -> &[u8] {
        &self.data[self.start..self.end]
    }
}

impl std::ops::Deref for ArcSlice {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        self.as_ref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readers_and_slices() {
        let b = [1u8, 2, 3, 4, 5];
        assert_eq!(le16(&b, 3), Some(0x0504));
        assert_eq!(le16(&b, 4), None);
        assert_eq!(le32(&b, 1), Some(0x05040302));
        assert_eq!(rd32(&b, 3), 0x0504);
        assert_eq!(rd16(&b, 4), 5);
        let a = ArcSlice::new(Arc::from(&b[..]));
        let s = a.slice(1, 4).slice(1, 10);
        assert_eq!(&*s, &[3, 4]);
        assert_eq!(s.offset(), 2);
        assert_eq!(deobfuscate(&[1, 11, 37, 0xff, 0x49]), "0aA -");
    }
}
