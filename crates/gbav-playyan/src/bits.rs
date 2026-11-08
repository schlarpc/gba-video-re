//! MSB-first bit reader shared by the bitstream decoders.

#[derive(Clone)]
pub struct BitReader<'a> {
    data: &'a [u8],
    pos: usize, // bit position
}

impl<'a> BitReader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        BitReader { data, pos: 0 }
    }
    pub fn at(data: &'a [u8], bitpos: usize) -> Self {
        BitReader { data, pos: bitpos }
    }
    #[inline]
    pub fn pos(&self) -> usize {
        self.pos
    }
    pub fn bits_left(&self) -> isize {
        (self.data.len() * 8) as isize - self.pos as isize
    }
    pub fn data(&self) -> &'a [u8] {
        self.data
    }
    /// Next 32 bits without consuming (zero past the end).
    #[inline]
    pub fn peek32(&self) -> u32 {
        let byte = self.pos >> 3;
        let mut w: u64 = 0;
        for i in 0..5 {
            w = w << 8 | *self.data.get(byte + i).unwrap_or(&0) as u64;
        }
        (w << (self.pos & 7) >> 8) as u32
    }
    #[inline]
    pub fn peek(&self, n: u32) -> u32 {
        if n == 0 {
            0
        } else {
            self.peek32() >> (32 - n)
        }
    }
    #[inline]
    pub fn skip(&mut self, n: u32) {
        self.pos += n as usize;
    }
    #[inline]
    pub fn get(&mut self, n: u32) -> u32 {
        let v = self.peek(n);
        self.pos += n as usize;
        v
    }
    #[inline]
    pub fn bit(&mut self) -> bool {
        self.get(1) != 0
    }
    pub fn align(&mut self) {
        self.pos = (self.pos + 7) & !7;
    }
    pub fn seek(&mut self, bitpos: usize) {
        self.pos = bitpos;
    }
}
