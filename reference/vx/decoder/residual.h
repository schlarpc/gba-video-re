/* residual.h -- interface between the VX++ macroblock decoder (vxdec.c, video agent)
 * and the residual decoder (residual.c, residual agent).
 *
 * OWNER of this header: video-bitstream agent. Implementation: residual agent.
 *
 * vx_residual() == IWRAM 0x03005650 residual_block (and its callee 0x03005794).
 * The caller has already written the block's prediction into the output frame; the
 * residual is decoded and ADDED in place (with the player's clamping).
 *
 * Arguments, in terms of the player's registers/state:
 *   bs     the shared bit reader (r1/r2/r3), see bits.h. Must be left positioned exactly
 *          after the residual syntax of this block.
 *   q      dequant scales, the 3 words the frame header stores at IWRAM 0x03005770..0x0300577b:
 *          q->scale[i] = LevelScale[QP%6][i] << (QP/6), LevelScale = 0x03000680
 *          {10,13,16},{11,14,18},{13,16,20},{14,18,23},{16,20,25},{18,23,29}.
 *          q->qp is informational (1..47).
 *   Y      pointer to the block's top-left luma sample in the OUTPUT frame
 *          (= [ctx+0xe4] + [ctx+0xec]), stride VX_STRIDE (256).
 *   UV     pointer to the block's top-left chroma PAIR in the output frame's interleaved
 *          chroma plane (= [ctx+0xe8] + [ctx+0xf0]); byte 0 = U, byte 1 = V, stride 256.
 *          For a luma WxH block the chroma area is (W/2)x(H/2) pairs = W bytes x H/2 rows.
 *   w, h   luma block size; 0x03005650 is only called with (16,16), (16,8), (8,16), (8,8)
 *          (fp = w, ip = h). It walks 8x8 luma units in raster order; per unit one ue(v)
 *          -> CBP byte table 0x03005610, bits 0..3 = luma 4x4 (offsets 0, 4, 4*256, 4*256+4),
 *          bit 4 = U 4x4, bit 5 = V 4x4.
 *
 * Coefficient VLC LUT: the player uses the 4096-entry u16 table at EWRAM 0x020010e4
 * (ROM file offset 0x9ae4). residual.c should define it as a literal array:
 *     extern const uint16_t vx_coef_lut[4096];
 * so the decoder needs no ROM for tables (it can then decode a bare .vx file).
 *
 * Return value: 0 on success, nonzero if an impossible code was met (debug aid only;
 * the player itself never fails).
 */
#ifndef VX_RESIDUAL_H
#define VX_RESIDUAL_H
#include <stdint.h>
#include "bits.h"

#define VX_STRIDE 256

typedef struct vx_qscale {
    int32_t scale[3];   /* IWRAM 0x03005770, 0x03005774, 0x03005778 */
    int qp;
} vx_qscale;

extern const uint16_t vx_coef_lut[4096];

int vx_residual(vx_bits *bs, const vx_qscale *q, uint8_t *Y, uint8_t *UV, int w, int h);

/* VXGB (Rev 5 player, IWRAM 0x03005eb0): same block sizes and Y/UV pointers; s[8] = the
 * per-position dequant scales (index (y&1)*4 + x); nzy / nzc = this block's entries in the
 * persistent luma (stride 0x41) / chroma (stride 0x21) nonzero-count grids, which it reads
 * (left and top neighbours) and writes. vxgb_residual.c, docs/vxgb.md. */
int vxgb_residual(vx_bits *bs, const int32_t s[8], uint8_t *Y, uint8_t *UV, uint8_t *nzy,
                  uint8_t *nzc, int w, int h);

#endif
