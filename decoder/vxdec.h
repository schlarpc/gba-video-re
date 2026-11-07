/* vxdec.h -- VX++ reference decoder state (see vxdec.c, notes/video.md). */
#ifndef VXDEC_H
#define VXDEC_H
#include <stdint.h>
#include <stdio.h>
#include "bits.h"
#include "residual.h"

typedef struct vx_dec {
    uint8_t ew[0x40000];      /* EWRAM image: offset = GBA address - 0x02000000 */
    vx_bits bs;
    vx_qscale q;
    const uint8_t *stream;    /* video elementary stream (VX header + 0x38) */
    size_t size;
    int width, height;
    uint32_t dec_index;       /* player ctx+0x28: frame n goes to slot n&3, refs (n-1..n-3)&3 */
    uint32_t refY[3], refC[3], outY, outC;   /* EWRAM offsets */
    uint32_t yoff, uvoff;     /* ctx+0xec / ctx+0xf0: current block offset in the planes */
    uint32_t mvp;             /* ctx+0xf4: MV-array entry of the current MB */
    /* codec variant and memory layout (VX++ Shrek defaults; VXGB set by vxgb_dec_init) */
    int vxgb;                 /* 1 = VXGB (Rev 5 player): no frame header, CAVLC residuals */
    uint32_t slot0, mvstart;  /* EWRAM offsets: frame slot 0, MV entry of MB (0,0) */
    uint32_t nzy, nzc;        /* VXGB: current block's luma / chroma nonzero-count grid entry */
    int32_t gscale[8];        /* VXGB: dequant scales from the header QP */
    uint32_t frame_bit;
    uint32_t hdr_q;           /* the frame header ue (0 = keep QP) */       /* bit position of the current frame's first bit */
    uint32_t *mb_bits;        /* optional: per-MB start bit positions */
    FILE *trace;
    unsigned long err, oob, fill_ovf, edge_intra;   /* fill_ovf: bilinear corner values outside 0..255 */
    unsigned long stat_modes[4][12];
} vx_dec;

void vx_dec_init(vx_dec *d, const uint8_t *stream, size_t size, int width, int height,
                 const uint8_t *ewram_image, size_t ewram_len);
void vxgb_dec_init(vx_dec *d, const uint8_t *stream, size_t size, int width, int height, int qp,
                   const uint8_t *ewram_image, size_t ewram_len);
void vx_dec_seek(vx_dec *d, uint32_t bitoff);
void vx_decode_frame(vx_dec *d);
const uint8_t *vx_frame_slot(vx_dec *d, uint32_t dec_index);
void vx_yuv_to_bgr555(const uint8_t *slot, uint16_t *out, int width, int height);
void vxgb_yuv_to_bgr555(const uint8_t *slot, uint16_t *out, int width, int height);
int vxgb_supported(void);   /* built with VXGB tables (gen_vxgb_tables.py from a Rev 5 ROM) */
void vx_yuv_to_bgr555_any(vx_dec *d, const uint8_t *slot, uint16_t *out);  /* picks the variant */
#endif
