//! Majesco shared layers, used by the Hydrogen engine (`gbav-hydrogen`) and the ADS engine
//! (`gbav-ads`):
//!
//! - [`vq`]: the VQ frame format (segment word, codebook unprediction, YCbCr -> BGR555 with the
//!   4x4 ordered dither and the brightness clamp, the blit for all 8 modes and line 159);
//! - [`adpcm`]: the ADPCM audio decoder with both context-table codings and both output stages;
//! - [`audio`]: the type-2 audio resource and an owning [`gbav_common::AudioDecoder`];
//! - [`frame`]: the decoded frame type both engines return.
//!
//! Specs: `docs/majesco/{vq,adpcm}.md`; reference C: `reference/majesco/{vq,adpcm}.c`.

pub mod adpcm;
pub mod audio;
pub mod frame;
pub mod vq;

pub use frame::VqFrame;
pub use gbav_common::bytes::{deobfuscate, rd16, rd32, ArcSlice};

/// Frame rate of every Majesco player: one frame every 5 VBlanks of 280896 cycles at 2^24 Hz.
pub const FPS_NUM: u64 = 1 << 24;
pub const FPS_DEN: u64 = 5 * 280896;
