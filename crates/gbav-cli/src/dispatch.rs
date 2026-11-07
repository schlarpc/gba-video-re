//! Per-family dispatch: run a generic job against the codec crate of a cart's family.
//!
//! Every command is written once, generically over [`gbav_common::Codec`]; this module picks the
//! codec type for a [`Family`] at run time. A family whose codec crate does not implement
//! `Codec` yet reports [`Error::Invalid`].

use gbav_common::{Codec, Error, Result};
use gbav_rom::Family;

/// A command body, generic over the codec.
pub trait Job {
    type Out;
    fn run<C: Codec>(self, rom: &[u8]) -> Result<Self::Out>;
}

/// Run `job` with the codec of `family`.
pub fn dispatch<J: Job>(family: Family, rom: &[u8], job: J) -> Result<J::Out> {
    // The codec crates are wired here as they implement gbav_common::Codec, e.g.
    //   Family::Vx => job.run::<gbav_vx::Vx>(rom),
    let _ = (rom, job);
    Err(Error::Invalid(format!(
        "no {} decoder is wired into this build of gbav yet",
        family.as_str()
    )))
}
