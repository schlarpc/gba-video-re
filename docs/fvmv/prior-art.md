# FVMV: prior art and how this work compares

## Sources

- quatric, "The Mobiclip Formats", section 8.4 "FVMV (Nintendo / Pokemon)":
  https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860
- quatric/mobipeg, `doc/gba_video_fvmv.md` (state 2026-08-08), `libavformat` FVMV demuxer and
  `libavcodec/fvmvdec.c`, checked at commit d5bb54fb (2026-11-04):
  https://github.com/quatric/mobipeg
- MultimediaWiki has no FVMV page.

mobipeg **does not reimplement the codec**. Its FVMV video and audio decoders find the
cartridge's 32 KiB IWRAM image in the ROM and execute the original ARM routines (0x03005fe8,
0x03001b88, 0x03001d18) under libunicorn. Its documentation describes the container and names
the three video substreams, but says that "a clean C translation" is still open. Before this
work there was therefore no written specification of the FVMV video or audio bitstream, and
no native decoder.

## Element by element

| Element | Prior art | This work | Verdict |
|---|---|---|---|
| Attribution | gist: "Nintendo", "neither Majesco's nor Actimagine's stack"; mobipeg doc: "Nintendo / Pokemon" | The player shows a **"powered by actimagine"** logo at boot. Its audio output stage is algorithmically identical to Actimagine's VX player: the same 4x interpolation weights and the same `(e[n-1] + 2 e[n]) * 15/16` noise shaper (no code bytes are shared). The pulse layout (3-bit pulses, stride 3, 2-bit start) is VX audio mode 0. The header maker code is "01" (Nintendo) | **Corrected.** Published by Nintendo; the codec and player are Actimagine's. It is a different and simpler video codec than VX, with the same audio lineage |
| Stream inventory (8 streams, frame counts) | yes | same | agrees |
| Frame rate | "8 fps" | 1704 scanlines per frame = **7.99171 fps**; the header field at +0x10 is that period ("player clock value" in mobipeg) | **corrected / new** |
| Header +0x18 | "configuration, unnamed" | size of the largest record; never read | new |
| Header +0x1c | "configuration, unnamed" | sizes the decoded-PCM buffer (2 x value bytes) | new |
| Record header +0x04 / +0x08 | "player timing/control value" | `back` / `fwd`: offsets to the previous and next **seek point**. Seek points are true keyframes (all 994 verified) and drive FF / rewind / skip | **new** |
| Video payload size | "compressed payload is video_size - 4 bytes; the bit reader can read up to four bytes beyond it" | `vlen` is the payload size. The bit and colour streams are consumed exactly, and the MV stream ends with 0-3 padding bytes. The bit reader never reads past `nbits` in any of the 80,972 frames | **corrected** |
| Substreams | "control / literals / indices_and_motion" | bit stream (u32 words, MSB-first, first bit discarded) / RGB555 colours and offsets / MV bytes | agrees, now fully specified |
| Block grammar, modes, MV table, edge rules, pixel arithmetic | "recursive block grammar over 8x8 cells; leaves copy, apply indexed motion, interpolate neighbours or reconstruct literal colors" | full grammar for all 15 block sizes, 8 operations with exact 32-bit arithmetic (bit 15 is state), MV table formula, row wrap of vectors (about 3-4 per frame), memory layout | **new** |
| Audio extra blocks | "each stream carries 1.25 seconds of audio priming past the video duration" | the extra blocks are one per 125 frames (frame 0 and frames 125n - 1). They correct the audio rate to the 7.9917 fps frame clock (1001/1000); they are not priming | **corrected** |
| Audio codec | "40-byte block -> 256 intermediate samples -> 1024 s8 samples" (executed, not described) | GSM 06.10 RPE-LPC without LTP: field layout, GSM table derivation (exact), 21 pulses per 64 samples, non-saturating lattice, de-emphasis 28180, 4x interpolation and noise-shaped quantiser, state never reset | **new** |
| Player (menus, timing, IRQs, input, A/V sync) | not covered | [player.md](player.md) | new |
| Build differences | "same entry signature and ABI" in all volumes | two builds (Vol 1/2, Vol 3/4); they differ only in the playback UI loop; the codec is identical | refined |
| Verification | frame 25 of Vol 1 stream 0 matches mGBA; 30 audio packets match the Python reference | C decoder bit-exact on every frame and audio block of all 8 streams against whole-stream mGBA captures, plus differential fuzzing against the cartridge code | new depth |

## Notes on mobipeg's approach

Executing the original routines is correct by construction, but it also carries the decoder's
memory-model quirks. Because the streams use row-wrapping motion vectors (section 3.6 of
[format.md](format.md)), any native port has to keep linear addressing in the reference buffer;
a port that pads or clamps the reference would fail on about 3-4 blocks per frame. mobipeg's
emulation path is not affected.

No prose or code from mobipeg or the gist was used here. They were read only as hints, and
every point above was checked against the cartridge code.
