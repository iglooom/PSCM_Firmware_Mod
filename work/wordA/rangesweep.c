/* Exhaustive (start,end) RANGE sweep for the PSCM 14C217 blk1 word A.
 *
 * WHY THIS SHAPE
 * --------------
 * find_crc_tables.py proved there is NO CRC table in blk0 or blk1 - the only
 * 256-entry table in the whole corpus is the REFLECTED CCITT (poly 0x1021)
 * table living in blk2 at +0x20EC/+0x2112/+0x21F2.  blk2 is the boot/second
 * image, so the routine that validates blk1 almost certainly runs there and
 * uses that one table.  Algorithm is therefore very likely fixed:
 *      CRC-16, poly 0x1021, reflected (and we also try non-reflected)
 * and the only unknown left is WHICH BYTES it covers.
 *
 * So: fix the polynomial, sweep EVERY (start,end) pair.
 *
 * The affine/XOR-difference trick again removes init and xorout:
 *      A_i ^ A_j == crc_{0,0}( m_i[s:e] ^ m_j[s:e] )
 * For a fixed start we get every end in ONE linear pass, so the whole
 * (start,end) space costs O(n^2/2) table steps - a few seconds per config
 * across 30 threads for n = 0x80000.
 *
 * Build: gcc -O3 -march=native -fopenmp -o rangesweep rangesweep.c
 * Run:   ./rangesweep <msgsets.bin> [step]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MAXV 4

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

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "msgsets.bin";
    int step = argc > 2 ? atoi(argv[2]) : 2;
    int want_poly = argc > 3 ? (int)strtol(argv[3], NULL, 0) : 0x1021;

    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return 1; }
    uint32_t nset;
    if (fread(&nset, 4, 1, f) != 1) return 1;

    /* we only need ONE set: the longest / most complete message per version */
    for (uint32_t s = 0; s < nset; s++) {
        uint8_t nl; uint32_t nver, len;
        if (fread(&nl, 1, 1, f) != 1) return 1;
        if (fread(&nver, 4, 1, f) != 1) return 1;
        if (fread(&len, 4, 1, f) != 1) return 1;
        char name[64];
        if (fread(name, 1, nl, f) != nl) return 1;
        name[nl] = 0;
        uint8_t *msg[MAXV];
        uint16_t tle[MAXV], tbe[MAXV];
        for (uint32_t v = 0; v < nver; v++) {
            uint8_t tb[2];
            if (fread(tb, 1, 2, f) != 2) return 1;
            tle[v] = (uint16_t)(tb[0] | (tb[1] << 8));
            tbe[v] = (uint16_t)((tb[0] << 8) | tb[1]);
            msg[v] = malloc(len);
            if (fread(msg[v], 1, len, f) != len) return 1;
        }
        fprintf(stderr, "== set %s nver=%u len=0x%X step=%d poly=0x%04X\n",
                name, nver, len, step, want_poly);

        /* 4 message transforms (covers wordwise MSB/LSB and bit-reflected word) */
        static const char *TRN[4] = {"plain", "bswap16", "rev8", "bswap16+rev8"};
        uint8_t R8[256];
        for (int i = 0; i < 256; i++) R8[i] = rev8v((uint8_t)i);

        uint16_t tmsb[256], tlsb[256];
        mk_tables((uint16_t)want_poly, tmsb, tlsb);

        for (int tr = 0; tr < 4; tr++) {
            /* build transformed diffs vs version 0 */
            uint8_t *diff[MAXV];
            uint16_t dle[MAXV], dbe[MAXV];
            for (uint32_t v = 1; v < nver; v++) {
                diff[v] = malloc(len);
                for (uint32_t i = 0; i < len; i++) {
                    uint8_t a = msg[0][i], bb = msg[v][i];
                    if (tr == 1 || tr == 3) {          /* byteswap pairs */
                        uint32_t j = (i ^ 1u) < len ? (i ^ 1u) : i;
                        a = msg[0][j]; bb = msg[v][j];
                    }
                    if (tr == 2 || tr == 3) { a = R8[a]; bb = R8[bb]; }
                    diff[v][i] = (uint8_t)(a ^ bb);
                }
                dle[v] = tle[0] ^ tle[v];
                dbe[v] = tbe[0] ^ tbe[v];
            }
            long long hits = 0;
#pragma omp parallel for schedule(dynamic, 8) reduction(+:hits)
            for (uint32_t st = 0; st < len; st += step) {
                /* running CRC of each version's diff from this start */
                uint16_t c_msb[MAXV], c_lsb[MAXV];
                for (uint32_t v = 1; v < nver; v++) { c_msb[v] = 0; c_lsb[v] = 0; }
                for (uint32_t e = st; e < len; e++) {
                    for (uint32_t v = 1; v < nver; v++) {
                        uint8_t x = diff[v][e];
                        c_msb[v] = (uint16_t)(c_msb[v] << 8) ^ tmsb[(uint8_t)((c_msb[v] >> 8) ^ x)];
                        c_lsb[v] = (c_lsb[v] >> 8) ^ tlsb[(uint8_t)(c_lsb[v] ^ x)];
                    }
                    if (((e + 1 - st) & 1) != 0) continue;   /* even lengths only */
                    int okm_le = 1, okm_be = 1, okl_le = 1, okl_be = 1;
                    for (uint32_t v = 1; v < nver; v++) {
                        if (c_msb[v] != dle[v]) okm_le = 0;
                        if (c_msb[v] != dbe[v]) okm_be = 0;
                        if (c_lsb[v] != dle[v]) okl_le = 0;
                        if (c_lsb[v] != dbe[v]) okl_be = 0;
                    }
                    if (okm_le | okm_be | okl_le | okl_be) {
#pragma omp critical
                        {
                            if (okm_le) printf("HIT tr=%s core=msb st=0x%X end=0x%X len=0x%X store=LE\n", TRN[tr], st, e + 1, e + 1 - st);
                            if (okm_be) printf("HIT tr=%s core=msb st=0x%X end=0x%X len=0x%X store=BE\n", TRN[tr], st, e + 1, e + 1 - st);
                            if (okl_le) printf("HIT tr=%s core=lsb st=0x%X end=0x%X len=0x%X store=LE\n", TRN[tr], st, e + 1, e + 1 - st);
                            if (okl_be) printf("HIT tr=%s core=lsb st=0x%X end=0x%X len=0x%X store=BE\n", TRN[tr], st, e + 1, e + 1 - st);
                            fflush(stdout);
                        }
                        hits++;
                    }
                }
            }
            fprintf(stderr, "   tr=%s done hits=%lld\n", TRN[tr], hits);
            for (uint32_t v = 1; v < nver; v++) free(diff[v]);
        }
        for (uint32_t v = 0; v < nver; v++) free(msg[v]);
    }
    fclose(f);
    printf("DONE\n");
    return 0;
}
