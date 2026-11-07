//! Per-family dispatch: run a generic job against the codec crate of a cart's family.
//!
//! Every command is written once, generically over [`gbav_common::Codec`]; this module picks the
//! codec type for a [`Family`] at run time.

use gbav_common::{Codec, Result, Rom};
use gbav_rom::Family;

/// A command body, generic over the codec.
pub trait Job {
    type Out;
    fn run<C: Codec>(self, rom: &Rom) -> Result<Self::Out>;
}

/// Run `job` with the codec of `family`. The ADS codec also covers the two Hydrogen-engine carts
/// that carry ADS (LZMA) streams; gbav-rom classifies those as `ads`.
pub fn dispatch<J: Job>(family: Family, rom: &Rom, job: J) -> Result<J::Out> {
    match family {
        Family::Vx => job.run::<gbav_vx::Vx>(rom),
        Family::Fvmv => job.run::<gbav_fvmv::Fvmv>(rom),
        Family::Hydrogen => job.run::<gbav_hydrogen::Hydrogen>(rom),
        Family::Ads => job.run::<gbav_ads::Ads>(rom),
    }
}
