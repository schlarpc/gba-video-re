# Majesco shared components

Majesco's GBA Video carts come in two engines: **Hydrogen** (Majesco's "Hydrogen Library", a DEFLATE
variant for compression; [../hydrogen/README.md](../hydrogen/README.md)) and **ADS** (an SFCD
archive of `.mmstr` streams with LZMA compression; [../ads/README.md](../ads/README.md)). Two carts
mix them: Jimmy Neutron 1 and the Yu-Gi-Oh! Noah's Final Threat proto use the Hydrogen engine with
the LZMA codec.

Above and below the compression layer the two families run the same algorithms, so the reference
code is shared:

| component | spec | code | used by |
|---|---|---|---|
| VQ video: codebook unprediction, YCbCr->RGB555 with ordered dither and brightness gain, blit | [vq.md](vq.md) | `reference/majesco/vq.{c,h}` | `reference/hydrogen` (hydec), `reference/ads` (adsdec) |
| ADPCM audio: VLC-coded adaptive ADPCM, 16458-sample blocks, 16384 Hz; `MJ_CTAB_VLC` (ADS palette-coded context table) or `MJ_CTAB_FLAT` (Hydrogen and the Hydrogen-engine LZMA carts) | [adpcm.md](adpcm.md) | `reference/majesco/adpcm.{c,h}` | `reference/hydrogen` (libhydec, hydemux), `reference/ads` (adsdec) |

Not shared: the compression layer (Hydrogen DEFLATE variant in `reference/hydrogen/hyinflate.c`,
ADS LZMA in `reference/ads/lzma_dec.c`) and the containers.

The Sonic X Vol 2 proto is a Hydrogen build with its own reference decoder
(`reference/sonicx2-proto`); its ADPCM differs from the shared one only by a missing output clamp
([../sonicx2-proto/README.md](../sonicx2-proto/README.md)).

| document | contents |
|---|---|
| [vq.md](vq.md) | the VQ layer, bit-exact spec |
| [adpcm.md](adpcm.md) | the ADPCM bitstream, both context-table codings, the output stages and the API |
| [ads-vs-hydrogen.md](ads-vs-hydrogen.md) | how each family uses the shared code, stream header layouts, the Hydrogen-engine LZMA carts, brightness bias, ADPCM framing details |
