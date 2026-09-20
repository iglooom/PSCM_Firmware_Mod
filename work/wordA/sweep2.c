/* Exhaustive CRC-16 identification for the Ford PSCM 14C217 blk1 word A.  v2
 *
 * METHOD
 * ------
 * 1) A table-driven CRC is affine over GF(2):
 *        crc_{init,xorout}(m) = crc_{0,0}(m) ^ K(len)
 *    so for two versions of the same part with EQUAL-LENGTH messages
 *        A1 ^ A2 == crc_{0,0}(m1 ^ m2)
 *    The init and xorout axes CANCEL. We therefore sweep ALL 65536 polynomials
 *    instead of a hand-picked catalogue, and only survivors get an init solve.
 *
 * 2) A "wordwise" 16-bit CRC is NOT a new algorithm: feeding a 16-bit word and
 *    running 16 shift steps is identical to feeding its two bytes through the
 *    ordinary bytewise core.  (Verified empirically: the v1 control matched
 *    both byte_lsb and word_lsb_le with the same K.)  MSB-first consumes
 *    high-byte-first, LSB-first low-byte-first, and a bit-reflected word equals
 *    byteswap + rev8 of each byte.  So every wordwise variant is covered by
 *    {msb,lsb} x {message as-is, byteswapped, rev8'd, byteswapped+rev8'd}.
 *    That makes the whole sweep table-driven, ~30x faster than v1.
 *
 * Stored-word endianness LE and BE are both tested.
 *
 * Build: gcc -O3 -march=native -fopenmp -o sweep2 sweep2.c
 * Run:   OMP_NUM_THREADS=30 ./sweep2 msgsets.bin
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define NTR 4      /* message transforms */
#define NCORE 2    /* msb / lsb */

static const char *TRNAME[NTR] = {"plain", "bswap16", "rev8", "bswap16+rev8"};
static const char *CORENAME[NCORE] = {"msb_first", "lsb_first"};

typedef struct {
    char name[40];
    int nver;
    size_t len;
    uint16_t tgt_le[4], tgt_be[4];
    uint8_t *msg[NTR][4];      /* transformed messages, per version */
    uint8_t *diff[NTR][4];     /* msg[t][v] ^ msg[t][0], v>=1 */
    uint16_t dtgt_le[4], dtgt_be[4];
} mset_t;

static uint16_t rev16(uint16_t v) {
    uint16_t r = 0;
    for (int i = 0; i < 16; i++) if (v & (1u << i)) r |= 1u << (15 - i);
    return r;
}
static uint8_t rev8v(uint8_t v) {
    uint8_t r = 0;
    for (int i = 0; i < 8; i++) if (v & (1u << i)) r |= 1u << (7 - i);
    return r;
}

static inline uint16_t crc_msb(const uint8_t *d, size_t n, uint16_t init, const uint16_t *t) {
    uint16_t c = init;
    for (size_t i = 0; i < n; i++) c = (uint16_t)(c << 8) ^ t[(uint8_t)((c >> 8) ^ d[i])];
    return c;
}
static inline uint16_t crc_lsb(const uint8_t *d, size_t n, uint16_t init, const uint16_t *t) {
    uint16_t c = init;
    for (size_t i = 0; i < n; i++) c = (c >> 8) ^ t[(uint8_t)(c ^ d[i])];
    return c;
}
static inline uint16_t crc_run(int core, const uint8_t *d, size_t n, uint16_t init,
                               const uint16_t *tmsb, const uint16_t *tlsb) {
    return core == 0 ? crc_msb(d, n, init, tmsb) : crc_lsb(d, n, init, tlsb);
}

static void mk_tables(uint16_t poly, uint16_t *tmsb, uint16_t *tlsb) {
    uint16_t rp = rev16(poly);
    for (int i = 0; i < 256; i++) {
        uint16_t c = (uint16_t)(i << 8);
        for (int k = 0; k < 8; k++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ poly) : (uint16_t)(c << 1);
        tmsb[i] = c;
        uint16_t e = (uint16_t)i;
        for (int k = 0; k < 8; k++) e = (e & 1) ? (uint16_t)((e >> 1) ^ rp) : (uint16_t)(e >> 1);
        tlsb[i] = e;
    }
}

static void transform(int t, const uint8_t *src, uint8_t *dst, size_t n) {
    static uint8_t R8[256];
    static int init8 = 0;
    if (!init8) { for (int i = 0; i < 256; i++) R8[i] = rev8v((uint8_t)i); init8 = 1; }
    if (t == 0) { memcpy(dst, src, n); return; }
    if (t == 1) {                               /* byteswap 16-bit pairs */
        size_t m = n & ~(size_t)1;
        for (size_t i = 0; i < m; i += 2) { dst[i] = src[i + 1]; dst[i + 1] = src[i]; }
        if (n & 1) dst[n - 1] = src[n - 1];
        return;
    }
    if (t == 2) { for (size_t i = 0; i < n; i++) dst[i] = R8[src[i]]; return; }
    {                                           /* byteswap + rev8 */
        size_t m = n & ~(size_t)1;
        for (size_t i = 0; i < m; i += 2) { dst[i] = R8[src[i + 1]]; dst[i + 1] = R8[src[i]]; }
        if (n & 1) dst[n - 1] = R8[src[n - 1]];
    }
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "msgsets.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return 1; }
    uint32_t nset;
    if (fread(&nset, 4, 1, f) != 1) return 1;
    mset_t *S = calloc(nset, sizeof(mset_t));
    size_t mem = 0;
    for (uint32_t s = 0; s < nset; s++) {
        uint8_t nl; uint32_t nver, len;
        if (fread(&nl, 1, 1, f) != 1) return 1;
        if (fread(&nver, 4, 1, f) != 1) return 1;
        if (fread(&len, 4, 1, f) != 1) return 1;
        if (nl >= sizeof(S[s].name)) { fprintf(stderr, "name too long\n"); return 1; }
        if (fread(S[s].name, 1, nl, f) != nl) return 1;
        S[s].name[nl] = 0;
        S[s].nver = (int)nver; S[s].len = len;
        uint8_t *raw[4];
        for (uint32_t v = 0; v < nver; v++) {
            uint8_t tb[2];
            if (fread(tb, 1, 2, f) != 2) return 1;
            S[s].tgt_le[v] = (uint16_t)(tb[0] | (tb[1] << 8));
            S[s].tgt_be[v] = (uint16_t)((tb[0] << 8) | tb[1]);
            raw[v] = malloc(len);
            if (fread(raw[v], 1, len, f) != len) return 1;
        }
        for (int t = 0; t < NTR; t++)
            for (int v = 0; v < S[s].nver; v++) {
                S[s].msg[t][v] = malloc(len); mem += len;
                transform(t, raw[v], S[s].msg[t][v], len);
            }
        for (int t = 0; t < NTR; t++)
            for (int v = 1; v < S[s].nver; v++) {
                S[s].diff[t][v] = malloc(len); mem += len;
                for (size_t i = 0; i < len; i++)
                    S[s].diff[t][v][i] = S[s].msg[t][0][i] ^ S[s].msg[t][v][i];
            }
        for (int v = 1; v < S[s].nver; v++) {
            S[s].dtgt_le[v] = S[s].tgt_le[0] ^ S[s].tgt_le[v];
            S[s].dtgt_be[v] = S[s].tgt_be[0] ^ S[s].tgt_be[v];
        }
        for (uint32_t v = 0; v < nver; v++) free(raw[v]);
        fprintf(stderr, "set %-18s nver=%d len=0x%zX\n", S[s].name, S[s].nver, S[s].len);
    }
    fclose(f);
    fprintf(stderr, "loaded %u sets, %.1f MB\n", nset, mem / 1048576.0);

    long long hits = 0;
#pragma omp parallel reduction(+:hits)
    {
        uint16_t tmsb[256], tlsb[256];
#pragma omp for schedule(dynamic, 16)
        for (int poly = 0; poly < 65536; poly++) {
            mk_tables((uint16_t)poly, tmsb, tlsb);
            for (uint32_t s = 0; s < nset; s++) {
                for (int t = 0; t < NTR; t++) {
                    for (int core = 0; core < NCORE; core++) {
                        int ok_le = 1, ok_be = 1;
                        for (int v = 1; v < S[s].nver; v++) {
                            uint16_t dc = crc_run(core, S[s].diff[t][v], S[s].len, 0, tmsb, tlsb);
                            if (dc != S[s].dtgt_le[v]) ok_le = 0;
                            if (dc != S[s].dtgt_be[v]) ok_be = 0;
                            if (!ok_le && !ok_be) break;
                        }
                        if (!ok_le && !ok_be) continue;
                        uint16_t c0 = crc_run(core, S[s].msg[t][0], S[s].len, 0, tmsb, tlsb);
#pragma omp critical
                        {
                            if (ok_le)
                                printf("HIT set=%s tr=%s core=%s poly=0x%04X store=LE K=0x%04X\n",
                                       S[s].name, TRNAME[t], CORENAME[core], poly,
                                       (uint16_t)(c0 ^ S[s].tgt_le[0]));
                            if (ok_be)
                                printf("HIT set=%s tr=%s core=%s poly=0x%04X store=BE K=0x%04X\n",
                                       S[s].name, TRNAME[t], CORENAME[core], poly,
                                       (uint16_t)(c0 ^ S[s].tgt_be[0]));
                            fflush(stdout);
                        }
                        hits += (ok_le ? 1 : 0) + (ok_be ? 1 : 0);
                    }
                }
            }
            if ((poly & 0x3FF) == 0) fprintf(stderr, "poly 0x%04X\n", poly);
        }
    }
    printf("DONE hits=%lld\n", hits);
    fflush(stdout);
    return 0;
}
