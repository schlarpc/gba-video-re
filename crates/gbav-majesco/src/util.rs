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
