//! Play-Yan family codecs, bit-exact against the cartridge's Alphamosaic VC01 firmware.
//!
//! Unlike the GBA Video cartridges, a Play-Yan carries no content: the media come from an SD
//! card and are decoded by firmware the GBA uploads to the cartridge's VC01. Decoding therefore
//! needs two inputs: a media file and a firmware source ([`firmware::Firmware`]: the user's
//! Play-Yan / Play-Yan Micro ROM or `playan.fup`). Non-standard tables come from the firmware.
//!
//! Modules: containers ([`asf`], [`mp4`], [`mp3file`]), codecs ([`m4v`] MPEG-4 Part 2 SP with
//! the firmware [`idct`], [`g726`], [`mp3`], [`aac`], [`nmp`] = the Nintendo MP3 Player's DSP
//! decoder), output stages ([`pixel`]: fit + YUV->RGB565 + the cartridge window's BGR555 swap,
//! thumbnails; [`gbapcm`]: the 8-bit GBA speaker stream), [`player`] (whole files as the GBA
//! receives them) and [`verify`] (the full-system hash manifests).

// The decoders mirror the C references and the firmware loop for loop; index loops and wide
// signatures are kept.
#![allow(clippy::needless_range_loop, clippy::too_many_arguments)]

pub mod bits;
pub mod firmware;

// containers
pub mod asf;
pub mod mp3file;
pub mod mp4;

// codecs
pub mod aac;
pub mod g726;
pub mod idct;
pub mod m4v;
pub mod m4v_tables;
pub mod mp3;
pub mod nmp;

// output stages
pub mod gbapcm;
pub mod pixel;

// whole-file playback
pub mod player;
pub mod verify;

pub use firmware::{Firmware, Image, ImageId};
