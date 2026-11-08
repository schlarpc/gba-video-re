/* The music firmware's vector kernels, written out from their algorithms (see the Rust
 * src/mp3/imdct.rs and src/mp3/dct32.rs for the description): IMDCT long (0xa96c) and short
 * (0x12446) with window and overlap, and the dct32 matrixing (0x49c). Multiply constants are read
 * from the firmware's instruction immediates at the addresses below (no firmware bytes here).
 * All additions wrap at 32 bits. */
#ifndef PYMP3_KERNELS_H
#define PYMP3_KERNELS_H
#include <stdint.h>

typedef struct { int32_t c0; uint32_t c1, c2; } mp3k_t;
/* {function id, c2, c1, c0 instruction as 2 * offset + form}; function -1 = window tap of 1.0 */
typedef int32_t kaddr_t[4];
static const uint32_t MP3_FUNC_BASE[3] = {0xa96c, 0x12446, 0x49c};
#define KU {-1, 0, 0, 0}

static const kaddr_t L_NINE[6] = {{0, 1328, 1348, 1444}, {0, 1712, 1732, 1828}, {0, 2096, 2116, 2212},
	{0, 5424, 5444, 5540}, {0, 5808, 5828, 5924}, {0, 6192, 6212, 6308}};
static const kaddr_t L_SQRT3 = {0, 5072, 5092, 5188};
static const kaddr_t L_HALF_SQRT2 = {0, 13104, 13124, 13220};
static const kaddr_t L_SEC[9] = {{0, 17744, 17764, 17860}, {0, 18928, 18948, 19044}, {0, 20112, 20132, 20228},
	{0, 21296, 21316, 21412}, KU, {0, 23248, 23268, 23364}, {0, 24432, 24452, 24548}, {0, 25616, 25636, 25732},
	{0, 26800, 26820, 26916}};
static const kaddr_t L_POST_P[9] = {{0, 18128, 18148, 18244}, {0, 19312, 19332, 19428}, {0, 20496, 20516, 20612},
	{0, 21680, 21700, 21796}, {0, 22448, 22468, 22564}, {0, 23632, 23652, 23748}, {0, 24816, 24836, 24932},
	{0, 26000, 26020, 26116}, {0, 27184, 27204, 27300}};
static const kaddr_t L_POST_Q[9] = {{0, 18512, 18532, 18628}, {0, 19696, 19716, 19812}, {0, 20880, 20900, 20996},
	{0, 22064, 22084, 22180}, {0, 22832, 22852, 22948}, {0, 24016, 24036, 24132}, {0, 25200, 25220, 25316},
	{0, 26384, 26404, 26500}, {0, 27568, 27588, 27684}};
#define LW_O {0, 28020, 28040, 28137}, {0, 28456, 28476, 28572}, {0, 28932, 28952, 29048}, {0, 29400, 29420, 29516}, \
	{0, 29868, 29888, 29984}, {0, 30332, 30352, 30448}, {0, 30800, 30820, 30916}, {0, 31268, 31288, 31384}, \
	{0, 31736, 31756, 31852}, {0, 32180, 32200, 32296}, {0, 32624, 32644, 32740}, {0, 33068, 33088, 33184}, \
	{0, 33512, 33532, 33628}, {0, 33956, 33976, 34072}, {0, 34400, 34420, 34516}, {0, 34840, 34860, 34956}, \
	{0, 35280, 35300, 35396}, {0, 35720, 35740, 35836}
#define LW_V {0, 36160, 36180, 36276}, {0, 36536, 36556, 36652}, {0, 36924, 36944, 37040}, {0, 37312, 37332, 37428}, \
	{0, 54660, 54680, 54776}, {0, 58000, 58020, 58116}, {0, 58380, 58400, 58496}, {0, 58760, 58780, 58876}, \
	{0, 59140, 59160, 59256}, {0, 59520, 59540, 59636}, {0, 59900, 59920, 60016}, {0, 60280, 60300, 60396}, \
	{0, 60660, 60680, 60776}, {0, 61040, 61060, 61156}, {0, 61420, 61440, 61536}, {0, 61796, 61816, 61912}, \
	{0, 62172, 62192, 62288}, {0, 62548, 62568, 62665}
static const kaddr_t L_WIN[3][36] = {
	{LW_O, LW_V},
	{LW_O, KU, KU, KU, KU, KU, KU, {0, 44976, 44996, 45092}, {0, 46420, 46440, 46536}, {0, 46800, 46820, 46916},
	 {0, 47180, 47200, 47296}, {0, 47560, 47580, 47676}, {0, 47940, 47960, 48056}, {0, 48320, 48341, 48421},
	 {0, 48652, 48673, 48753}, {0, 48984, 49005, 49085}, {0, 49312, 49333, 49413}, {0, 49640, 49661, 49741},
	 {0, 49968, 49989, 50069}},
	{{0, 48320, 48341, 48421}, {0, 48652, 48673, 48753}, {0, 48984, 49005, 49085}, {0, 49312, 49333, 49413},
	 {0, 49640, 49661, 49741}, {0, 49968, 49989, 50069}, {0, 52836, 52856, 52952}, {0, 53304, 53324, 53420},
	 {0, 53772, 53792, 53888}, {0, 54216, 54236, 54332}, {0, 54660, 54680, 54776}, {0, 55104, 55124, 55220},
	 KU, KU, KU, KU, KU, KU, {0, 56084, 56104, 56200}, {0, 56460, 56480, 56576}, {0, 56848, 56868, 56964},
	 {0, 57236, 57256, 57352}, {0, 57624, 57644, 57740}, {0, 58000, 58020, 58116}, {0, 58380, 58400, 58496},
	 {0, 58760, 58780, 58876}, {0, 59140, 59160, 59256}, {0, 59520, 59540, 59636}, {0, 59900, 59920, 60016},
	 {0, 60280, 60300, 60396}, {0, 60660, 60680, 60776}, {0, 61040, 61060, 61156}, {0, 61420, 61440, 61536},
	 {0, 61796, 61816, 61912}, {0, 62172, 62192, 62288}, {0, 62548, 62568, 62665}},
};
static const kaddr_t S_HALF = {1, 660, 681, 760};
static const kaddr_t S_SQRT3_2 = {1, 996, 1016, 1112};
static const kaddr_t S_SEC[3] = {{1, 2996, 3016, 3112}, {1, 2644, 2664, 2760}, {1, 2292, 2312, 2408}};
static const kaddr_t S_POST_P[3] = {{1, 3612, 3632, 3728}, {1, 3964, 3984, 4080}, {1, 4316, 4336, 4432}};
static const kaddr_t S_POST_Q[3] = {{1, 5020, 5040, 5136}, {1, 4668, 4688, 4784}, {1, 7484, 7504, 7600}};
static const kaddr_t S_WIN[12] = {{1, 9596, 9616, 9712}, {1, 7836, 7856, 7952}, {1, 8188, 8208, 8304},
	{1, 8540, 8560, 8656}, {1, 8892, 8912, 9008}, {1, 9244, 9264, 9360}, {1, 6780, 6800, 6896},
	{1, 6076, 6096, 6192}, {1, 5372, 5392, 5488}, {1, 5724, 5744, 5840}, {1, 6428, 6448, 6544},
	{1, 7132, 7152, 7248}};
/* 2cos(k pi / 64), k = 1..31 */
static const kaddr_t D_COS[31] = {{2, 92, 112, 208}, {2, 7468, 7488, 7584}, {2, 556, 576, 672},
	{2, 10796, 10816, 10912}, {2, 1020, 1040, 1136}, {2, 7884, 7904, 8000}, {2, 1484, 1504, 1600},
	{2, 14124, 14144, 14240}, {2, 1948, 1968, 2064}, {2, 8716, 8736, 8832}, {2, 2412, 2432, 2528},
	{2, 11212, 11232, 11328}, {2, 2876, 2896, 2992}, {2, 8300, 8320, 8416}, {2, 3340, 3360, 3456},
	{2, 17476, 17496, 17592}, {2, 3804, 3824, 3920}, {2, 9964, 9984, 10080}, {2, 4268, 4288, 4384},
	{2, 12044, 12064, 12160}, {2, 4732, 4752, 4848}, {2, 10380, 10400, 10496}, {2, 5196, 5216, 5312},
	{2, 14540, 14560, 14656}, {2, 5660, 5680, 5776}, {2, 9548, 9568, 9664}, {2, 6124, 6144, 6240},
	{2, 11628, 11648, 11744}, {2, 6588, 6608, 6704}, {2, 9132, 9152, 9248}, {2, 7052, 7072, 7169}};

typedef struct {
	mp3k_t nine[6], sqrt3, half_sqrt2, sec[9], post_p[9], post_q[9];
	mp3k_t win[3][36]; uint8_t unit[3][36];
	mp3k_t s_half, s_sqrt3_2, s_sec[3], s_post_p[3], s_post_q[3], s_win[12];
	mp3k_t ck[32];   /* 2cos(k pi / 64), [0] unused */
} mp3consts;

#define A32(a, b) ((int32_t)((uint32_t)(a) + (uint32_t)(b)))
#define S32(a, b) ((int32_t)((uint32_t)(a) - (uint32_t)(b)))

static inline int32_t mp3_mulfw(int32_t x, const mp3k_t* k) {
	int64_t H = x >> 16, L = (int64_t)(x & 0xffff);
	int64_t acc = H * ((int64_t)k->c0 * 65536 + k->c1) + L * k->c0 + ((uint64_t)((L >> 8) * k->c2) & 0xffff);
	return (int32_t)(uint32_t)(acc >> 8);
}

/* one 9-point half: A[0..4] (A4 = x0 - x2 + x4 - x6 + x8), B[0..3] */
static void mp3_nine(const int32_t* x, const mp3consts* k, int32_t* A, int32_t* B) {
	const mp3k_t *a = &k->nine[0], *b = &k->nine[1], *c = &k->nine[2], *d = &k->nine[3], *e = &k->nine[4], *f = &k->nine[5];
	int32_t x0x2 = A32(x[0], x[0]), t = A32(x0x2, x[6]);
	A[0] = A32(A32(A32(t, mp3_mulfw(x[2], a)), mp3_mulfw(x[4], b)), mp3_mulfw(x[8], c));
	A[1] = S32(S32(S32(A32(x0x2, x[2]), x[4]), A32(x[6], x[6])), x[8]);
	A[2] = A32(S32(S32(t, mp3_mulfw(x[2], c)), mp3_mulfw(x[4], a)), mp3_mulfw(x[8], b));
	A[3] = S32(A32(S32(t, mp3_mulfw(x[2], b)), mp3_mulfw(x[4], c)), mp3_mulfw(x[8], a));
	A[4] = A32(S32(A32(S32(x[0], x[2]), x[4]), x[6]), x[8]);
	int32_t s3 = mp3_mulfw(x[3], &k->sqrt3);
	B[0] = A32(A32(A32(mp3_mulfw(x[1], d), s3), mp3_mulfw(x[5], e)), mp3_mulfw(x[7], f));
	B[1] = mp3_mulfw(S32(S32(x[1], x[5]), x[7]), &k->sqrt3);
	B[2] = A32(S32(S32(mp3_mulfw(x[1], e), s3), mp3_mulfw(x[5], f)), mp3_mulfw(x[7], d));
	B[3] = S32(A32(S32(mp3_mulfw(x[1], f), s3), mp3_mulfw(x[5], d)), mp3_mulfw(x[7], e));
}

static int32_t mp3_side(const int32_t* A, const int32_t* B, int j) {
	return j < 4 ? A32(A[j], B[j]) : j == 4 ? A[4] : S32(A[8 - j], B[8 - j]);
}

/* long-block IMDCT; w = window index (block type 0 -> 0, 1 -> 1, 3 -> 2) */
static void mp3_imdct_long(const int32_t* X, const int32_t* ov, int32_t* out, int32_t* nov, int w, const mp3consts* k) {
	int32_t s[18], e[9], o[9], EA[5], EB[4], OA[5], OB[4], p[9], q[9];
	for (int i = 0; i < 18; i++) s[i] = X[i];
	for (int i = 17; i >= 1; i--) s[i] = A32(s[i], s[i - 1]);
	for (int i = 17; i >= 3; i -= 2) s[i] = A32(s[i], s[i - 2]);
	for (int i = 0; i < 9; i++) { e[i] = s[2 * i]; o[i] = s[2 * i + 1]; }
	mp3_nine(e, k, EA, EB);
	mp3_nine(o, k, OA, OB);
	OA[4] = mp3_mulfw(OA[4], &k->half_sqrt2);
	for (int j = 0; j < 9; j++) {
		int32_t ev = mp3_side(EA, EB, j);
		int32_t od = j == 4 ? OA[4] : mp3_mulfw(mp3_side(OA, OB, j), &k->sec[j]);
		p[j] = mp3_mulfw(A32(ev, od), &k->post_p[j]);
		q[j] = mp3_mulfw(S32(ev, od), &k->post_q[j]);
	}
	for (int i = 0; i < 18; i++) {
		int32_t y = i < 9 ? S32(0, q[8 - i]) : q[i - 9];
		if (!k->unit[w][i]) y = mp3_mulfw(y, &k->win[w][i]);
		out[i] = A32(y, ov[i]);
		int32_t z = i < 9 ? p[8 - i] : p[i - 9];
		nov[i] = k->unit[w][18 + i] ? z : mp3_mulfw(z, &k->win[w][18 + i]);
	}
}

static void mp3_imdct_short(const int32_t* X, const int32_t* ov, int32_t* out, int32_t* nov, const mp3consts* k) {
	int32_t buf[36] = {0};
	for (int w = 0; w < 3; w++) {
		const int32_t* v = X + 6 * w;
		int32_t a0 = v[0], a1 = A32(v[1], v[0]), a2 = A32(v[2], v[1]), a3 = A32(A32(v[3], v[2]), a1);
		int32_t a4 = A32(v[4], v[3]), a5 = A32(A32(A32(v[5], v[4]), v[3]), v[2]);
		int32_t t = A32(a0, mp3_mulfw(a4, &k->s_half)), s = mp3_mulfw(a2, &k->s_sqrt3_2);
		int32_t ep = A32(t, s), em = S32(a0, a4), en = S32(t, s);
		t = A32(a1, mp3_mulfw(a5, &k->s_half)); s = mp3_mulfw(a3, &k->s_sqrt3_2);
		int32_t op = mp3_mulfw(A32(t, s), &k->s_sec[0]);
		int32_t om = mp3_mulfw(S32(a1, a5), &k->s_sec[1]);
		int32_t on = mp3_mulfw(S32(t, s), &k->s_sec[2]);
		int32_t P0 = mp3_mulfw(A32(ep, op), &k->s_post_p[0]), Q0 = mp3_mulfw(S32(ep, op), &k->s_post_q[0]);
		int32_t P1 = mp3_mulfw(A32(em, om), &k->s_post_p[1]), Q1 = mp3_mulfw(S32(em, om), &k->s_post_q[1]);
		int32_t P2 = mp3_mulfw(A32(en, on), &k->s_post_p[2]), Q2 = mp3_mulfw(S32(en, on), &k->s_post_q[2]);
		const int32_t src[12] = {Q2, Q1, Q0, Q0, Q1, Q2, P2, P1, P0, P0, P1, P2};
		for (int i = 0; i < 12; i++) buf[6 + 6 * w + i] = A32(buf[6 + 6 * w + i], mp3_mulfw(src[i], &k->s_win[i]));
	}
	for (int i = 0; i < 18; i++) { out[i] = A32(buf[i], ov[i]); nov[i] = buf[18 + i]; }
}

/* Lee's DCT recursion with depth tracking (see dct32.rs) */
static void mp3_dct(const int32_t* x, int n, int d, int32_t* out, int8_t* dep, const mp3consts* k) {
	if (n == 1) { out[0] = x[0]; dep[0] = (int8_t)d; return; }
	int h = n / 2;
	int32_t a[16] = {0}, b[16] = {0}, ao[16], bo[16];
	int8_t ad[16], bd[16];
	for (int i = 0; i < h; i++) {
		a[i] = A32(x[i], x[n - 1 - i]);
		b[i] = mp3_mulfw(S32(x[i], x[n - 1 - i]), &k->ck[(2 * i + 1) * 32 / n]);
	}
	mp3_dct(a, h, d, ao, ad, k);
	mp3_dct(b, h, d + 1, bo, bd, k);
	for (int m = 0; m < h; m++) { out[2 * m] = ao[m]; dep[2 * m] = ad[m]; }
	out[1] = bo[0]; dep[1] = bd[0];
	for (int m = 1; m < h; m++) { out[2 * m + 1] = S32(bo[m], out[2 * m - 1]); dep[2 * m + 1] = dep[2 * m - 1]; }
}

static void mp3_dct32(const int32_t* x, int32_t* row, const mp3consts* k) {
	int32_t o[32]; int8_t d[32];
	mp3_dct(x, 32, 0, o, d, k);
#define TWO(i) (d[i] == 0 ? (int32_t)((uint32_t)o[i] << 1) : o[i])
	for (int r = 0; r < 16; r++) row[r] = TWO(16 + r);
	row[16] = 0;
	for (int j = 0; j < 17; j++) row[17 + j] = S32(0, TWO(16 - j));
#undef TWO
}
#endif
