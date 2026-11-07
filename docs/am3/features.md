# AM3 feature matrix

Columns are player builds. On AM3 the player (`AEXE`) ships on each card, so builds are
identified per card from the descrambled AEXE (its hash).
No card has been seen yet, so the only build that can be described is the adapter loader.
"card" = needs a dump (see [README.md](README.md)).

| Feature | Adapter loader 1.0.620 (firmware) | Player build(s) on cards |
|---|---|---|
| Role | Boot, card access, signed-code loader | Movie player (UI, copy protection, decoders) |
| Code location | Firmware 12 KiB -> IWRAM 0x03000000 | Card file index 0, `AEXE` chunk -> EWRAM 0x02000000 (<= 256 KiB) |
| Code authentication | RSA-2048, e = 3, Barrett; SHA-1 over the stored AEXE; no padding check | card |
| Code scramble | 32-bit Galois LFSR XOR, mask 0xA3000000, seed from the RSA result | card |
| Copy protection | none in the firmware | `00.AM3` + 128-bit SmartMedia ID, "DES" (prior art) |
| Container | chunk stream {fourcc, u32 size, payload}, LE | card |
| Colour space | (n/a; loader display: mode 4, 2-colour progress bar) | card |
| Block sizes / transform | n/a | card |
| Intra / inter, motion compensation | n/a | card |
| Entropy coding / compression layer | n/a | card |
| Rate control / quantiser | n/a | card |
| Keyframes / seek | n/a | card (prior art: player supports fast-forward and rewind) |
| Audio codec | n/a | card |
| Media access | adapter commands 0x09 (fw), 0x01 (card), 0x0B (reg write); 1 KiB window | same adapter; GamePak IRQ per transfer (prior art) |
| Toolchain fingerprint | ARM C library divide/memcpy identical to FVMV (Pokemon) carts | card |
