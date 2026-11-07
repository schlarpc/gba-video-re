//! File writers for decoded output: PNG (uncompressed DEFLATE), PPM, raw bytes and WAV.

use gbav_common::gzip::{crc32, zlib_stored};
use std::io::{self, Write};
use std::path::Path;

/// BGR555 (GBA) to 8-bit RGB, expanding 5 bits by replicating the top bits.
pub fn bgr555_to_rgb8(px: &[u16]) -> Vec<u8> {
    let mut out = Vec::with_capacity(px.len() * 3);
    for &p in px {
        for shift in [0, 5, 10] {
            let c = ((p >> shift) & 31) as u8;
            out.push((c << 3) | (c >> 2));
        }
    }
    out
}

fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    let start = out.len();
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let crc = crc32(0, &out[start..]);
    out.extend_from_slice(&crc.to_be_bytes());
}

/// An 8-bit RGB PNG with stored (uncompressed) DEFLATE blocks.
pub fn png_rgb8(width: u32, height: u32, rgb: &[u8]) -> Vec<u8> {
    assert_eq!(rgb.len(), (width * height * 3) as usize);
    let mut raw = Vec::with_capacity(rgb.len() + height as usize);
    for row in rgb.chunks(width as usize * 3) {
        raw.push(0); // filter: none
        raw.extend_from_slice(row);
    }
    let mut png = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&width.to_be_bytes());
    ihdr.extend_from_slice(&height.to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]); // 8-bit, truecolour, deflate, adaptive, no interlace
    chunk(&mut png, b"IHDR", &ihdr);
    chunk(&mut png, b"IDAT", &zlib_stored(&raw));
    chunk(&mut png, b"IEND", &[]);
    png
}

/// A binary PPM (P6).
pub fn ppm_rgb8(width: u32, height: u32, rgb: &[u8]) -> Vec<u8> {
    let mut out = format!("P6\n{width} {height}\n255\n").into_bytes();
    out.extend_from_slice(rgb);
    out
}

/// Streaming 16-bit mono WAV writer; the header is patched on `finish`.
pub struct WavWriter {
    file: std::fs::File,
    samples: u32,
}

impl WavWriter {
    pub fn create(path: &Path, rate: u32) -> io::Result<WavWriter> {
        let mut file = std::fs::File::create(path)?;
        file.write_all(&wav_header(rate, 0))?;
        Ok(WavWriter { file, samples: 0 })
    }

    pub fn write(&mut self, pcm: &[i16]) -> io::Result<()> {
        let mut b = Vec::with_capacity(pcm.len() * 2);
        for s in pcm {
            b.extend_from_slice(&s.to_le_bytes());
        }
        self.samples += pcm.len() as u32;
        self.file.write_all(&b)
    }

    pub fn finish(mut self, rate: u32) -> io::Result<()> {
        use std::io::Seek;
        self.file.seek(io::SeekFrom::Start(0))?;
        self.file.write_all(&wav_header(rate, self.samples))
    }
}

fn wav_header(rate: u32, samples: u32) -> Vec<u8> {
    let data = samples * 2;
    let mut h = Vec::with_capacity(44);
    h.extend_from_slice(b"RIFF");
    h.extend_from_slice(&(36 + data).to_le_bytes());
    h.extend_from_slice(b"WAVEfmt ");
    h.extend_from_slice(&16u32.to_le_bytes());
    h.extend_from_slice(&1u16.to_le_bytes()); // PCM
    h.extend_from_slice(&1u16.to_le_bytes()); // mono
    h.extend_from_slice(&rate.to_le_bytes());
    h.extend_from_slice(&(rate * 2).to_le_bytes());
    h.extend_from_slice(&2u16.to_le_bytes());
    h.extend_from_slice(&16u16.to_le_bytes());
    h.extend_from_slice(b"data");
    h.extend_from_slice(&data.to_le_bytes());
    h
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn colour_expansion() {
        assert_eq!(bgr555_to_rgb8(&[0x7fff, 0x001f, 0x7c00]), [255, 255, 255, 255, 0, 0, 0, 0, 255]);
    }

    #[test]
    fn png_structure() {
        let png = png_rgb8(2, 1, &[1, 2, 3, 4, 5, 6]);
        assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
        assert_eq!(&png[12..16], b"IHDR");
        // IHDR CRC over type + data, as computed by any PNG reader
        assert_eq!(&png[29..33], &crc32(0, &png[12..29]).to_be_bytes());
        // IDAT holds a zlib stream whose stored payload is the filtered scanline
        let idat_len = u32::from_be_bytes(png[33..37].try_into().unwrap()) as usize;
        let z = &png[41..41 + idat_len];
        let (raw, _) = gbav_common::gzip::inflate(&z[2..]).unwrap();
        assert_eq!(raw, [0, 1, 2, 3, 4, 5, 6]);
        assert!(png.ends_with(&[0, 0, 0, 0, b'I', b'E', b'N', b'D', 0xae, 0x42, 0x60, 0x82]));
    }
}
