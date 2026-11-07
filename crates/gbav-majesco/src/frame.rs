//! A decoded Majesco frame: the framebuffer after the blit, plus what the manifests hash.

use crate::vq::{H, W};
use gbav_common::Frame;

/// One frame as the cartridge leaves it in VRAM after the blit.
#[derive(Clone, Debug)]
pub struct VqFrame {
    /// stream frame number
    pub frame: u32,
    /// segment (chunk) index in the stream
    pub segment: u32,
    /// block geometry of the segment
    pub mode: u8,
    /// lines the blit wrote from the top (159 or 160)
    pub lines: usize,
    /// the frame's index plane (one byte per block)
    pub index: Vec<u8>,
    /// 240x160 BGR555, row-major
    pub fb: Vec<u16>,
    bytes: Vec<u8>,
}

impl VqFrame {
    pub fn new(frame: u32, segment: u32, mode: u8, lines: usize, index: &[u8], fb: &[u16]) -> VqFrame {
        let mut bytes = Vec::with_capacity(W * H * 2);
        for &p in fb {
            bytes.extend_from_slice(&p.to_le_bytes());
        }
        VqFrame { frame, segment, mode, lines, index: index.to_vec(), fb: fb.to_vec(), bytes }
    }
    /// The whole 240x160 framebuffer, little-endian BGR555.
    pub fn fb_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

impl Frame for VqFrame {
    /// The framebuffer lines the blit wrote (all 160 lines on the shipped players, 159 in the
    /// 3-row-block modes on the Sonic X Vol 2 prototype), little-endian BGR555.
    fn manifest_bytes(&self) -> &[u8] {
        &self.bytes[..self.lines * W * 2]
    }
    fn bgr555(&self) -> Vec<u16> {
        self.fb.clone()
    }
}
