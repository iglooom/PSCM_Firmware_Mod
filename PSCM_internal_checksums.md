# Ford PSCM (EPAS) — internal checksum analysis of 14C217 / 14C218 / 14C386

**Status: ALL 6 internal checksum words SOLVED and verified.**

> **🎉 Round 3 (2026-09-13): `14C217` blk1 word A is SOLVED.**
>
> ```
> word_A = CRC-16/MCRF4XX( blk0 ++ blk1[START .. 0x63FEA) )      # init 0xFFFF
>        stored little-endian at blk1+0x63FEA  (flash 0x0007FFEA)
> ```
>
> i.e. reflected CCITT, poly `0x1021`, init `0xFFFF`, xorout `0x0000` — over
> **blk0 concatenated with the tail of blk1**, *skipping the 14C218 calibration* that sits between
> them in flash. `START` is **per build** and must be read from the firmware's own code (§8.0).
>
> Verified on all three OEM versions **and on the live vehicle's 512 KB flash dump**.
> Reference implementation: `work/wordA/wordA_solved.py` (self-verifying, exit 0).
>
> **Patching blk0/blk1 is now unblocked** — see §10.

ECU: PSCM / electric power steering, CAN address `0x730`, core is a Freescale **MC56F8366**
(confirmed from the physical chip, 2026-09-13; 512KB PFlash / 32KB DFlash / 4KB PRAM / 32KB DRAM)
(16-bit DSP — confirmed by the `14C386` VBF description string: *"Generated with SPT MC56F8
Metrowerks CodeWarrior V3.04.02"*).

Analysis instrument: multiple OEM versions of each part, spanning 2011–2017. All findings below
reproduce **exactly on every file** of their part, so no match is a coincidence.

---

## 1. The corpus

| File | Part | Type | vbf | Load / size | Build stamp |
|---|---|---|---|---|---|
| `BV6T-14C217-AF.vbf` | BV6T-14C217-AF | EXE | 2.3 | 3 blocks | 2011-08-23 11:14:14 |
| `CV6T-14C217-AH.vbf` | CV6T-14C217-AH | EXE | 2.3 | 3 blocks | 2013-01-15 09:23:26 |
| `CV6T-14C217-AR.VBF` | CV6T-14C217-AR | EXE | 2.3 | 3 blocks | 2016-06-22 14:53:51 |
| `BV6T-14C218-AF.vbf` | BV6T-14C218-AF | DATA | 2.2 | `0x00009800` + `0x12800` | — |
| `CV6T-14C218-AX.VBF` | CV6T-14C218-AX | DATA | 2.2 | `0x00009800` + `0x12800` | — |
| `BV6T-14C386-AA.vbf` | BV6T-14C386-AA | SIGCFG | 2.3 | `0x04008000` + `0x788` | — |
| `CV6T-14C386-AB.vbf` | CV6T-14C386-AB | SIGCFG | 2.3 | `0x04008000` + `0x79C` | — |

All 7 pass `vbftool verify` (block CRC-16/CCITT-FALSE + file CRC-32, 0 trailing bytes).
Container CRCs are *not* the ECU's integrity layer — the words below are.

### The complete flash map

The three parts are complementary and together tile the flash. **This is the key insight** that
unlocked the 14C217 block-1 sum:

```
0x00000000 ┌──────────────────────────┐
           │ 14C217 blk0   0x09800    │  code
0x00009800 ├──────────────────────────┤
           │ 14C218 blk0   0x12800    │  calibration  <- the "gap" in the 217-only analysis
0x0001C000 ├──────────────────────────┤
           │ 14C217 blk1   0x64000    │  main application  ...footer at 0x0007FFE4
0x00080000 └──────────────────────────┘

0x04008000 ┌──────────────────────────┐
           │ 14C386 blk0   ~0x790     │  SIGCFG  ...CRC at its own end
0x04008C00 ├──────────────────────────┤
           │ 14C217 blk2   0x07400    │  2nd image  ...footer at 0x0400FFF2
0x04010000 └──────────────────────────┘
```

---

## 2. Common footer layout (14C217 blk1 and blk2)

```
[ 6-byte BCD timestamp ][ word A : 2 B ][ word B : 2 B ][ padding ]
       DD MM YY HH MM SS   CRC-16          SUM-16
```

Both words are stored **little-endian**, and in both blocks the SUM-16 range
**includes the CRC word A** that precedes it.

---

## 3. 14C386 (SIGCFG) — SOLVED

Single trailing word, no timestamp.

| Field | Location | Algorithm |
|---|---|---|
| CRC-16 | last 2 bytes of the block (`len-2`), **little-endian** | **CRC-16/CCITT-FALSE** — poly `0x1021`, **non-reflected**, init `0xFFFF`, xorout `0x0000`, over `block[0 .. len-2)` |

Note this is the *non-reflected* CCITT variant — a **different** CRC from the one 14C217 blk2 uses.

| Version | Block len | CRC offset | Stored | Computed |
|---|---|---|---|---|
| BV6T-14C386-AA | `0x788` | `0x786` (`0x04008786`) | `239E` | `239E` OK |
| CV6T-14C386-AB | `0x79C` | `0x79A` (`0x0400879A`) | `A629` | `A629` OK |

Block length differs between versions, so the CRC offset is **not fixed** — it is always
`block_length - 2`. (Both blocks sit in the same erase region of `0xC00`.)

---

## 4. 14C218 (DATA / calibration) — SOLVED

This part's checksum is a **complement word at the START of the block**, not the end.

| Field | Location | Algorithm |
|---|---|---|
| SUM-16 complement | first 2 bytes (`0x00009800`), **little-endian** | `word = 0xFFFF - sum16le(block[2 .. end])` |

Equivalently, and this is the property the ECU almost certainly tests:

> **`sum16le(entire 14C218 block) == 0xFFFF`**

| Version | Stored @`0x9800` | `0xFFFF - sum16le(rest)` | Whole-block sum |
|---|---|---|---|
| BV6T-14C218-AF | `9E34` | `9E34` OK | `FFFF` OK |
| CV6T-14C218-AX | `4309` | `4309` OK | `FFFF` OK |

The block also carries its ASCII part number at offset `0x127E6`, and the final 2 bytes
(`0138` / `5B03`) are **not** a checksum — they are data covered by the sum.

---

## 5. 14C217 block 2 — SOLVED (both words)

Footer at flash `0x0400FFF2` (block offset `0x73F2`):

| Flash addr | Offset | Size | Field |
|---|---|---|---|
| `0x0400FFF2` | `0x73F2` | 2 | constant marker `00 46` (all 3 files) |
| `0x0400FFF4` | `0x73F4` | 6 | BCD timestamp |
| `0x0400FFFA` | `0x73FA` | 2 | **word A — CRC-16** (LE) |
| `0x0400FFFC` | `0x73FC` | 2 | **word B — SUM-16** (LE) |
| `0x0400FFFE` | `0x73FE` | 2 | `FF FF` pad |

* **word A** = **CRC-16/MCRF4XX** — poly `0x1021`, **reflected**, init `0xFFFF`, xorout `0x0000`
  — over `blk2[0x00000 .. 0x073FA)`.
  *(Reflected — unlike 14C386's CRC. Both variants exist in this ECU.)*
* **word B** = `sum16le( blk2[0x00000 .. 0x073FC) )` — includes word A, excludes the `FFFF` pad.

| Version | A stored | A calc | B stored | B calc |
|---|---|---|---|---|
| BV6T-...-AF | `EFFD` | `EFFD` OK | `35E7` | `35E7` OK |
| CV6T-...-AH | `BFBA` | `BFBA` OK | `7472` | `7472` OK |
| CV6T-...-AR | `68A3` | `68A3` OK | `8655` | `8655` OK |

Independent confirmation: the CRC **residue** `crc16_reflected(blk2[0 .. 0x73FC), init=0)` is the
constant `0x4E05` on all three files — the signature of "data immediately followed by its own CRC".

---

## 6. 14C217 block 1 — SOLVED (both words)

Footer at flash `0x0007FFE4` (block offset `0x63FE4`):

| Flash addr | Offset | Size | Field |
|---|---|---|---|
| `0x0007FFE0` | `0x63FE0` | 4 | filler `0A E7 0A E7` |
| `0x0007FFE4` | `0x63FE4` | 6 | BCD timestamp |
| `0x0007FFEA` | `0x63FEA` | 2 | **word A — UNSOLVED** (LE) |
| `0x0007FFEC` | `0x63FEC` | 2 | **word B — SUM-16** (LE) |
| `0x0007FFEE` | `0x63FEE` | 6 | `00 E7 00 E7 00 00` (all 3 files) |
| `0x0007FFF4` | `0x63FF4` | 12 | zeros then `0A E7` filler to `0x00080000` |

### word B — whole-image SUM-16 (now exact, no fudge factor)

```
word_B = sum16le( linear_flash[0x00000000 .. 0x0007FFEC) )
```

where `linear_flash` is 14C217 blk0 ++ **14C218 blk0** ++ 14C217 blk1. This spans all three
lower-flash regions, which is why neither blk0 nor the 14C218 part needs a footer of its own —
**they are covered here**.

| Version (paired with its 14C218) | B stored | B calc |
|---|---|---|
| BV6T-14C217-AF + BV6T-14C218-AF | `C2AE` | `C2AE` OK |
| CV6T-14C217-AR + CV6T-14C218-AX | `3216` | `3216` OK |

> **Resolved open question from the previous revision.** The earlier 217-only model needed an
> unexplained `-1` correction term. With the real 14C218 data in the gap, the `-1` is explained
> exactly: `sum16le(14C218 block) == 0xFFFF ≡ -1 (mod 0x10000)`. The 14C218 region contributes
> exactly `-1` to any enclosing 16-bit sum — a deliberate design property (§4) that makes the
> calibration region *transparent* to the application's whole-image checksum. The formula now
> needs no correction term.

This also means: **a 14C218 recalibration does not invalidate the 14C217 block-1 word B**, as long
as the 14C218 part keeps its own `sum == 0xFFFF` property. Elegant, and worth remembering when
patching.

### word A — SOLVED (round 3)

```
word_A = crc16_mcrf4xx( blk0 ++ blk1[START .. 0x63FEA), init=0xFFFF )
```

**CRC-16/MCRF4XX** — poly `0x1021`, **reflected**, init `0xFFFF`, xorout `0x0000` — the *same*
engine as blk2's word A (§5), using the same table (`blk2+0x20EC`, reached as `X:$5676`).
Stored **little-endian**.

The message is a **concatenation of two non-adjacent flash regions**: all of blk0, then the tail
of blk1 from `START` — **skipping the 14C218 calibration** that lies between them
(`0x9800..0x1C000`).

| Version | `START` immediate | flash byte | blk1-relative | stored | computed |
|---|---|---|---|---|---|
| BV6T-...-AF | `#$0000E800` | `0x01D000` | `blk1+0x1000` | `C704` | `C704` OK |
| CV6T-...-AH | `#$0000E800` | `0x01D000` | `blk1+0x1000` | `A291` | `A291` OK |
| CV6T-...-AR | `#$0000EC00` | `0x01D800` | `blk1+0x1800` | `D110` | `D110` OK |

⚠ **`START` is per build.** Assuming `0x1000` gives `C8F0` instead of `D110` on AR. It must be read
from the firmware's own self-check code — `work/wordA/wordA_solved.py::find_start_offset()` does
this automatically by locating the `#$0003FFF5` (end = word A) immediate and taking the adjacent
long-immediate.

**Independently verified on the live vehicle's 512 KB flash dump** (`dumps/PSCM_pflash_*.bin`,
module `CV6T-14C217-AR`): computed `D110` = stored `D110`, and the wrong `START` reproduces the
`C8F0` mismatch exactly — confirming the per-build constant on real silicon.

> **Design symmetry with word B.** Word B *includes* the calibration but is made insensitive to it
> (`sum16le(14C218) == 0xFFFF`, §4). Word A *excludes* it outright. Both achieve the same goal: a
> recalibration must not invalidate the code checksums. Consistent, and reassuring that the
> reading is right.

#### Why every sweep missed it (worth remembering)

The message is a **concatenation that skips a gap**, which none of the search shapes could express:

* `rangesweep.c` swept *every* `(start,end)` pair — but **of blk1 alone**, so no blk0 prefix.
* the linear-image tests used one **contiguous** span, so they always included the calibration.
* `msgsets.bin` *did* contain `b0_b1_*` compositions — but that run was killed at **poly 64 of
  65536**, so poly `0x1021` was never reached on them.

**Lesson:** an "exhaustive" sweep is only exhaustive over the *shape* of message it can express.
Contiguous-range search cannot find a gapped concatenation no matter how many polynomials it tries.
When the algorithm is known but no range matches, vary the **composition**, not the parameters.

---

## 7. Exhaustive exclusions for blk1 word A (historical — superseded by §6/§8.0)

Every test demanded a simultaneous match on all available files (3 files for blk1-only ranges;
2 files where 14C218 gap data is required, since only 2 of the 3 eras have a matching 14C218).

* **CRC-16 catalogue, byte-wise**: polys `1021, 8005, 8408, A001, C002, 3D65, 8BB7, C867, 0589,
  A097, 1DCF, 5935, 2F15, 080B, 6F63` x {reflected, non-reflected} x init {`0000, FFFF, 1D0F,
  C6C6, B2AA, 89EC, 800D, 2233, 554D, 780C, 4C06, 6363`} x xorout {`0000, FFFF`} x stored
  endianness {LE, BE}.
  Ranges: `blk1[0..63FEA/63FEC/63FEE/64000/63FE0/63FE2)`, `blk0`, `blk0+blk1` (all cut points),
  `blk0+blk1+blk2`, `blk2+blk0+blk1`, `blk1+blk0`, byte-swapped (16-bit) variants, and variants
  with the checksum field zeroed / `FF`-filled. **No match.**
* **Same catalogue over the TRUE linear image** (with 14C218 in the gap), ranges
  `[0..7FFEA)`, `[0..7FFEC)`, `[0..7FFEE)`, `[0..80000)`, `[0..1C000)`, `[1C000..7FFEA)`,
  `[9800..7FFEA)`, `[9800..1C000)`, `[1C000..80000)`, plus concatenations with the upper image
  (14C386 + blk2) in both orders. **No match.**
* **CRC-32 catalogue** (`04C11DB7, 1EDC6F41, A833982B, 814141AB`) treating A‖B as one 32-bit
  word, all inits/xorouts/endiannesses, every prefix end. **No match.**
* **Reverse-CRC state walk** — unwinding the register backwards from the stored word to find a
  common init across files — for the whole polynomial set, on blk1, blk0+blk1, the gap-filled
  image (`FF` and `00` fill) and byte-swapped variants. **No common init at any start offset.**
* **Full GF(2) linear window sweep** over *every* (start, end) pair for the CCITT family: yields
  only ~24 windows, each with a nonsense start/end and an arbitrary per-window init — coincidences,
  no structure.
* **Additive / xor families** over every (start,end) window via prefix-sum hashing: sum8,
  sum16-LE/BE, sum32-LE/BE, xor8, xor16, one's-complement sum16, Fletcher-16 — over blk1,
  blk0+blk1, blk0+blk1+blk2 and the linear image, with ±1, ±2, negated and complemented target
  variants. **No match.**
  *(This same sweep is what found every solved word in this document, so the method is sound.)*
* **Arithmetic relation to its neighbours**: A vs B (`A+B`, `A^B`, `A-B`) and A vs the timestamp
  show no constant relation across versions. **Not a derived constant.**

### Round 2 (2026-09-13, later) — exhaustive sweeps, all negative

Tooling in `work/wordA/`. **Every tool was first validated against the known blk2 word A**
(§5) and had to re-derive `poly=0x1021, reflected, stored LE` *uniquely* before its negative
results were accepted. Both did. Details in §7.1.

Key method upgrade — **the affine trick**. A table-driven CRC satisfies
`crc_{init,xorout}(m) = crc_{0,0}(m) ^ K(len)`, so for two versions of the same part with
equal-length messages:

```
A_i ^ A_j == crc_{0,0}( m_i ^ m_j )
```

The **init and xorout axes cancel**. That removed two dimensions and made it affordable to
sweep **all 65536 polynomials** instead of the 15-poly catalogue used in round 1.

Second upgrade — a *word-wise* 16-bit CRC is **not** a distinct algorithm. Feeding a 16-bit word
and running 16 shift steps is identical to feeding its two bytes through the ordinary byte-wise
core; MSB-first consumes high-byte-first, LSB-first low-byte-first, and a bit-reflected word
equals byteswap + `rev8` of each byte. So the whole word-wise family (hypothesis 1 of round 1,
`work/hunt_wordA.py`) is covered by `{msb,lsb} x {plain, bswap16, rev8, bswap16+rev8}`.
**Hypothesis 1 is therefore closed — and it is NOT the answer.**

Newly excluded, every test demanding a simultaneous match on **all three** files:

* **All 65536 polys** x 4 transforms x `{msb,lsb}` x stored `{LE,BE}` over the
  disassembly-derived region `blk1[0x1000..0x63FEA)` (§8.1) — **0 hits**.
* **Exhaustive `(start,end)` range sweep** — *every* byte range of blk1, poly `0x1021`, 4
  transforms, both cores, both stored endiannesses (`work/wordA/rangesweep.c`, O(n²) in C).
  59 hits, **not one of them ends at `0x63FEA`** (word A's own offset), so all are
  coincidences of the ~16-bit birthday space. **Word A is not a CCITT CRC over any contiguous
  byte range of blk1.**
* **Additive / xor families** (sum16, xor16, one's-complement, sub16, Fletcher-16, rotate-xor),
  forwards and backwards, LE/BE word order, inits `{0,1,FFFF}`, with `raw / complement / negate
  / ±1 / 0xFFFF-x` adjustments, over the derived region and its boundary variants — **0 hits**.
* **Cross-block carriers** — full `(start,end)` sweep with blk1's word A as the target over
  `blk0`, `blk2`, `blk0+blk2`, `blk2+blk0` — only coincidences, none at a meaningful boundary.
* **Per-build seed, solved exactly rather than guessed.** For a CRC the map `init -> crc` is
  affine and invertible, so the init each version *would* need can be recovered by Gaussian
  elimination over GF(2) (`work/wordA/solve_seed.py`). Recovered inits are structureless — not
  the BCD timestamp, not the part number, not word B, not blk2's word A, and no constant offset
  between versions. **Hypothesis 2 substantially weakened.**
* **Short-region CRCs** (all 65536 polys) over the 6-byte timestamp, timestamp+filler,
  `blk1[:0x40]`, `blk1[:0x100]` — **0 hits**. (`blk1[:0x1000]` and `blk0[:0x100]` not finished.)

### The decisive structural clue: there is no CRC table in blk0/blk1

`work/wordA/find_crc_tables.py` reconstructs the 256-entry table for **all 65536 polynomials**
x `{msb,lsb}` x entry order `{LE,BE}` and memcmps it against every even offset of every image
in the corpus. The complete result:

| Image | Offset | Poly | Kind |
|---|---|---|---|
| BV6T-14C217-AF/blk2 | `0x20EC` | `0x1021` | reflected, LE entries |
| CV6T-14C217-AH/blk2 | `0x2112` | `0x1021` | reflected, LE entries |
| CV6T-14C217-AR/blk2 | `0x21F2` | `0x1021` | reflected, LE entries |

That is **the only CRC table in the P-space (program flash) blocks**. Two consequences:

1. **There is no CRC table in blk0 or blk1.**
2. ⚠ **Correction to §8 as previously written.** The claimed *non-reflected* CCITT table at
   `blk2+0x01F0 / +0x0208` is **not** a 256-entry CRC table — nothing at those offsets matches a
   reconstructed table for any polynomial. The §3 14C386 CRC is non-reflected CCITT, so it is
   presumably computed bitwise or from a partial/other-width table. Do not rely on the old
   "both variants are tabled" claim.

> ⚠⚠ **Round 3 correction — do NOT read this section as "word A is not a CRC".**
> That inference (recorded below as hypothesis 1) was **wrong**. Word A *is* a reflected CCITT
> CRC-16 — the disassembly proves it (§8.0). The reasoning failed because this scan searched the
> **P-space** blocks (blk0/blk1/blk2 as loaded program images) while the CRC routine indexes its
> table in **X-space** at `X:$5676`, which is `blk2+0x20EC` — the very table listed above, reached
> through a different address space. The negative results in this section remain valid as stated
> (no *contiguous byte range* of blk1 CRCs to word A), but the conclusion drawn from them did not
> follow: the input sequence, not the algorithm, was the unknown.
>
> **Lesson worth keeping:** on a dual-Harvard core, "no table in the code image" is not "no
> table" — enumerate P: *and* X: before concluding anything about the algorithm class.

### Remaining hypotheses, ranked (superseded by §8.0 — kept for the record)

1. ~~**Not a CRC at all**~~ — **REFUTED by disassembly (§8.0).** It is a table-driven reflected
   CRC-16, poly `0x1021`.
2. **Non-contiguous / chunked coverage** — **now the live question.** Every sweep assumed one
   contiguous span fed linearly; the real routine is unrolled and takes a count, and a sibling
   routine covers X-space. This is where the answer is.
3. **Covers something not in the VBFs** — still possible for part of the input (DFlash, the
   external SPI EEPROM).
4. ~~**Per-build seed**~~ — weakened in round 2 (solved inits are structureless), and §8.0 shows a
   fixed table with the seed set up in registers; low priority.
5. ~~A stored constant unrelated to flash content~~ — refuted: it is computed and compared at runtime.

**Brute force is exhausted and has been superseded by reading the code (§8.0).**

---

## 8. How word A was found: the disassembly trail

### 8.0 ⭐⭐ ROUND 3 — the algorithm, read out of the code

Full disassembly (`work/wordA/dis56800e.py`, 569 encodings extracted mechanically from the ERM)
resolved what brute force could not. Three linked discoveries:

**(a) The verifier** — `blk1+0x82A0`, a function present in all three versions:

```
P:$12150  E418 FFF5 0003   MOVE.L #$0003FFF5,R0    ; address of word A
P:$12153  8068             MOVE.W P:(R0)+,A        ; read STORED word A from flash
P:$12154  4C44 11EF        CMP.W  X:$11EF,A         ; compare with RAM cell X:$11EF
```

So **`X:$11EF` holds the computed checksum**. Word A is a pass/fail comparison against it.

**(b) The producer** — `X:$11EF` has exactly **one** store in all of blk1, at `blk1+0x8214`,
immediately after a call:

```
P:$12105  E255 6216        JSR    P:$16216          ; <-- computes the checksum
P:$1210A  D57C 11EF        MOVE.W Y0,X:$11EF         ; store result
```

**(c) The accumulator** — `P:$16216` (`blk1+0x1042C`) is an **unrolled table-driven CRC**:

```
P:$16216  8748 5676   MOVE.W #$5676,X0     ; CRC TABLE BASE = X:$5676
P:$1621C  856A        MOVE.W P:(R2)+,Y0    ; read a PROGRAM word
P:$1621E  7AFA        EOR.W  Y0,Y0         ; crc ^ data
P:$1621F  7ED2        ZXT.B  B,B           ; isolate a byte
P:$16221  8921        ADDA   Rn,Rn         ; table base + index
P:$16222  5FA8        LSRR.W #$08,D        ; crc >> 8
P:$16223  F501        MOVE.W X:(R1)+,Y0    ; TABLE LOOKUP (X: space)
P:$16224  7BDA        EOR.W  Y1,Y1         ; ^ table entry
```

i.e. the classic reflected form `crc = (crc >> 8) ^ tbl[(crc ^ byte) & 0xFF]`.

**The table address closes the loop on §7's "no table in blk0/blk1" finding.** `X:$5676` is in
**data flash**, not program flash: blk2 loads at `X:$4600`, so

```
X:$5676 - X:$4600 = 0x1076 words = 0x20EC bytes  ->  blk2+0x20EC
```

which is **exactly the reflected CCITT table already catalogued in §8.2**. Verified byte-exact
against a reconstructed poly-`0x1021` reflected table in all three versions. §7's scan missed it
only because it searched P-space blocks and the table lives in X-space.

> **ESTABLISHED:** word A is a **CRC-16, poly `0x1021`, reflected**, computed with the blk2
> table, over **program words read via `P:(R2)+`**, result staged in `X:$11EF`.
> A second, near-identical routine exists at `P:$1627C` (reached from `blk1+0x8254`) which reads
> **X-space** via `MOVEU.BP X:(R0)+` — i.e. the same CRC engine applied to data flash.

**NOT yet established — the exact byte feed.** Round 3b nailed down the *arguments* but not the
inner loop. From `work/wordA/find_calls.py`, the single meaningful call site is `blk1+0x820A`:

```
b0x081D4  MOVE.L #$0000E800,A    ; START word address -> byte 0x01D000
b0x081EE  MOVE.L #$000317F5,B    ; COUNT in WORDS
b0x0820A  JSR    P:$16216
```

and the count is **exact**: `0x317F5` = 202741 words = the region
`blk1[0x1000 .. 0x63FEA)` = 405482 bytes, ending precisely at word A.
`0xE800 + 0x317F5 = 0x3FFF5` (= word A). **The region is therefore certain.**

Yet feeding that region through the confirmed engine does **not** reproduce the stored words.
Tested (`work/wordA/test_wordcrc.py` and follow-up sweep), all demanding a 3-version match:

| Axis | Coverage |
|---|---|
| region | words_full, count-as-bytes, half-count, from blk1+0, to word B, to block end |
| byte order in each word | lo-then-hi, hi-then-lo |
| feed granularity | both bytes per word, low byte only, high byte only |
| engine | reflected (the confirmed table) and forward CCITT |
| **seed** | **all 65536** (solved via the affine trick, not guessed) |
| stored endianness / xorout | LE, BE, each with and without `^0xFFFF` |

> **Key deduction.** Sweeping *all* seeds also covers the **chaining**: `X:$11EF` is read into `Y1`
> **before** the JSR and stored from `Y0` after, so the routine is entered with whatever a previous
> stage left. If no seed reproduces the word for this region, then the incoming seed is not the
> problem — **the per-byte feed inside the loop is**. Hypotheses "unknown seed" and "chained from
> an earlier region" are therefore *both* eliminated for this region.

So the remaining unknown is narrow and concrete: the ~16-word loop body at `P:$16216` does
something other than plain `crc = (crc>>8) ^ tbl[(crc^byte)&0xFF]` on successive bytes. Raw words
(`work/wordA/raw_dump.py 0x16216`) for one unrolled iteration:

```
856A  MOVE.W P:(R2)+,Y0     ; one program word
7442  LSRR.W ...
7AFA  EOR.W  ...
7ED2  ZXT.B  ...            ; byte isolate
8905  MOVE.W DDDDD,HHHHH
8921  ADDA   Rn,Rn          ; table base + index
5FA8  LSRR.W #$08,...       ; crc >> 8
F501  MOVE.W X:(R1)+,Y0     ; table entry  <- first lookup
7BDA  EOR.W  ...
787A  EOR.W  ...            ; second byte-step begins
7C02  ZXT.B  ...
8910  MOVE.W DDDDD,HHHHH
8921  ADDA   Rn,Rn
5FA8  LSRR.W #$08,...
F001  MOVE.W X:(R1)+,A      ; table entry  <- second lookup
7B8A  EOR.W  ...
5481  SUB.W  #$01,...       ; count--
```

Two lookups per word read, confirming 2 bytes/word.

#### Round 3c — operand-level decode (`work/wordA/decode_word.py`)

Every field of every loop word resolved against ERM tables A-7/A-10..A-13. **`Y1` is the CRC
register**, `Y0` the data/table scratch, `R1` the table index, `R0` the table base, `R2` the
program pointer, `B` the loop counter:

| Word | Decoded | Role |
|---|---|---|
| `8748 5676` | `MOVE.W #$5676,R0` | table base `X:$5676` = `blk2+0x20EC` |
| `856A` | `MOVE.W P:(R2)+,Y0` | read one **program word** |
| `7AFA` | `EOR.W` Y0/Y1 (`E`=Y0, `a`=Y1) | fold data into CRC |
| `7ED2` | `ZXT.B Y0,Y0` | isolate a byte |
| `8905` | `MOVE.W R1,Y0` / `MOVEU.W` | index staging |
| `8921` | `ADDA R0,R1` | table base + index |
| `5FA8` | **`LSRR.W #8,Y1`** | **`crc >> 8`** — Y1 is the CRC |
| `F501` | `MOVE.W X:(R1)+,Y0` | **table lookup #1** |
| `7BDA` | `EOR.W` Y1/Y0 | `^ table entry` |
| `787A`,`7C02`,`8910`,`F001`,`7B8A` | second byte-step, lookup into `A` | **table lookup #2** |
| `5481` | `SUB.W #$01,B` | `count--` |

**The computing function is a resumable, WRAPPING state machine.** Decoded at `blk1+0x81AA`:

```
F27D 08F3        MOVE.L X:$08F3,C.L     ; RELOAD saved 32-bit program POINTER
E410 FFF5 0003   MOVE.L #$0003FFF5,A.L  ; A = END = word A
7827 / AD2F      CMP.L ... / branch     ; pointer reached the end?
5D43 4C00        CMP.L  #$4C00,...      ; chunk size 0x4C00 words
E401 4C00        MOVE.L #$4C00,B.L      ; B = words to do this pass
E410 E800 0000   MOVE.L #$0000E800,A.L  ; ...else RESET pointer to START 0xE800
F77C 11EF        MOVE.W X:$11EF,Y1      ; RELOAD running CRC
E255 6216        JSR    P:$16216        ; CRC one chunk
D57C 11EF        MOVE.W Y0,X:$11EF      ; save running CRC
D07D 08F3        MOVE.L A10.L,X:$08F3   ; save ADVANCED pointer
```

So a background task CRCs `0x4C00` words per invocation, walks `0xE800 -> 0x3FFF5`, then **wraps
back to `0xE800` and starts again** — a continuous integrity monitor, not a boot-time check.
`X:$08F3` holds the pointer, `X:$11EF` the CRC. On completion the value is snapshotted
(`blk1+0x8274`: `MOVE.W X:$11EF,X:$11E8`) and compared against the stored word at `blk1+0x82A8`.
**A second, independent accumulator `X:$11EA`** is driven the same way by `JSR P:$1627C` (X-space)
over data flash from `X`-byte `0x8C00` — **blk2's own load address**.

This matters for patching: the check is **not** one-shot at startup, so a wrong word A would fault
the module during normal operation, not only at boot.

> **Engine control (now passing).** The confirmed engine — reflected CCITT, poly `0x1021`, init
> `0xFFFF`, the blk2 table — **exactly reproduces blk2's known word A** on all three versions
> (`work/wordA/test_chunked.py` asserts this before testing anything else). So the *engine* is
> certainly right; only the blk1 *feed* is open.

**Operand direction, settled from the manual's own field naming.** `LSRR.W EEE,FFF` is the Rosetta
stone: its syntax is `src,dst`, and its grid is `0111 11FF Faaa 1001` — the uppercase `FFF` (the
*second* operand, i.e. the destination) sits at **bits 9-7**, so the lowercase field at **bits 6-4
is the source**. Same shape for `ZXT.B FFF,FFF` and `EOR.W EEE,EEE`. Applying that:

```
856A  MOVE.W P:(R2)+,Y0    ; Y0 = program word
7AFA  EOR.W  Y1,Y0         ; Y0 = crc ^ word
7ED2  ZXT.B  Y0,Y0         ; Y0 = (crc ^ word) & 0xFF     = table index
8905/8921                  ; R1 = R0 (table base) + index
5FA8  LSRR.W #8,Y1         ; Y1 = crc >> 8
F501  MOVE.W X:(R1)+,Y0    ; Y0 = tbl[index]
7BDA  EOR.W  Y0,Y1         ; crc = (crc >> 8) ^ tbl[index]
```

— exactly `crc = (crc >> 8) ^ tbl[(crc ^ byte) & 0xFF]`, low byte first, with the second unrolled
half repeating it for the high byte through accumulator `A`.

> **Equivalence proved, retiring a whole hypothesis family.** The code XORs the *full 16-bit word*
> into the CRC once and then runs two table steps. That looks like a distinct "word-wise" algorithm,
> but it is **numerically identical** to feeding the low byte then the high byte through the ordinary
> byte-wise core — verified for several seeds in `work/wordA/verify_engine.py`. So no separate
> word-wise variant existed to try, and engine + byte order were never the problem.

With engine, operand direction, byte order and all 65536 seeds pinned, **the region had to be
wrong** — and it was: the message is `blk0 ++ blk1[START:...]`, a gapped concatenation no
contiguous-range sweep could express. See **§6** for the solution and why the sweeps missed it.

### 8.1 The self-check routine (round 2)

This is the most actionable result of round 2. **The MC56F8366 is word-addressed**, so a flash
byte address `X` appears in code as the immediate `X/2`. Word A lives at byte `0x0007FFEA`, i.e.
**word `0x0003FFF5`** — and that immediate is present in blk1 of **all three** versions, three
times each, at *identical relative spacing* (`+0x2A`, `+0xC2`):

| Version | blk1 offsets of immediate `0x0003FFF5` |
|---|---|
| BV6T-14C217-AF | `0x81B4`, `0x81DE`, `0x82A0` |
| CV6T-14C217-AH | `0x94E4`, `0x950E`, `0x95D0` |
| CV6T-14C217-AR | `0x9E5A`, `0x9E84`, `0x9F46` |

Searching for the *word* form is what found this; round 1 searched only byte addresses, which is
why the routine was missed. Recovered by `work/wordA/find_regions.py`, which decodes the
DSP56800E long-immediate load `E41n <lo16> <hi16>` -> `MOVE.L #imm32,Rn`:

```
BV6T-14C217-AF blk1+0x81B4:  MOVE.L #$0003FFF5,R0   ; word A   -> byte 0x07FFEA
               blk1+0x81D4:  MOVE.L #$0000E800,R0   ; 0xE800   -> byte 0x01D000
               blk1+0x81DE:  MOVE.L #$0003FFF5,R1   ; word A
               blk1+0x81E8:  MOVE.L #$0000E800,R2   ; 0xE800
               blk1+0x81EE:  MOVE.L #$000317F5,R1   ; 0x317F5  -> byte 0x062FEA
```

and the three constants close **exactly**:

```
0x3FFF5 - 0xE800 = 0x317F5          (all in WORD units)
```

so they are a `(start, count, end)` region triple in words. The two readings are:

| Reading | Region in bytes | blk1-relative | Tested |
|---|---|---|---|
| start `0xE800`, count `0x317F5`, end word A | `[0x01D000 .. 0x07FFEA)` | `[0x1000 .. 0x63FEA)` | yes — all 65536 polys + additive: **0 hits** |
| start `0x317F5`, count `0xE800`, end word A | `[0x062FEA .. 0x07FFEA)` | `[0x46FEA .. 0x63FEA)` | yes — all 65536 polys + additive: **0 hits** |

Both are exhausted for CRC and additive families, which is exactly why hypothesis 1 in §7
("not a CRC at all") is now ranked first. **The region is no longer the unknown — the
accumulator is.**

> ⚠ Note the routine lives in **blk1 itself**, not blk2. So blk1 carries its own self-check, and
> the blk2 CCITT table is not necessarily involved in word A at all.

**Next step (highest value, and cheaper than any further brute force): disassemble forward from
`blk1+0x81B4` and simply read the accumulator.** `work/wordA/dis_blk1.py` dumps that window, but
`work/dsp56800e_dis.py` covers only the small opcode subset needed for the SBL work and renders
most of this routine as `.word`. Extending it for the arithmetic/loop opcodes (`ADD`, `EOR`,
`MAC`, `ROL`, `MOVE.W (Rn)+,X0`, `REP`/`DO` loop forms) from the DSP56800E/EX Core Reference
Manual Appendix A is the concrete task.

Supporting lead: the literal `0x0001C000` (big-endian) appears in blk1 at `0x680` (AF),
`0x8E8`/`0xA22` (AH), `0x6E8`/`0x85E` (AR) — candidate region-descriptor tables, and worth
re-checking against hypothesis 2 (non-contiguous coverage).

### 8.2 CRC machinery in blk2

Block 2 contains the one CRC table in the corpus — 256-entry, 16-bit little-endian, poly
`0x1021`, **reflected**:

| Version | reflected table |
|---|---|
| BV6T-14C217-AF | `blk2+0x20EC` (`0x0400ACEC`) |
| CV6T-14C217-AH | `blk2+0x2112` (`0x0400AD12`) |
| CV6T-14C217-AR | `blk2+0x21F2` (`0x0400ADF2`) |

It drives the blk2 footer CRC (§5). See §7 for the correction: the previously claimed
*non-reflected* table at `blk2+0x01F0/+0x0208` does not exist.

---

## 9. Reference implementation (verified against every file)

```python
import glob

def _mk16(poly, ref):
    t = []
    for i in range(256):
        if ref:
            rp = int('{:016b}'.format(poly)[::-1], 2); c = i
            for _ in range(8): c = (c >> 1) ^ (rp if c & 1 else 0)
        else:
            c = i << 8
            for _ in range(8): c = ((c << 1) ^ poly) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        t.append(c & 0xFFFF)
    return t

_T_REF, _T_FWD = _mk16(0x1021, 1), _mk16(0x1021, 0)

def crc16_mcrf4xx(d, init=0xFFFF):          # 14C217 blk2 word A
    c = init
    for x in d: c = (c >> 8) ^ _T_REF[(c ^ x) & 0xFF]
    return c

def crc16_ccitt_false(d, init=0xFFFF):      # 14C386
    c = init
    for x in d: c = ((c << 8) & 0xFFFF) ^ _T_FWD[((c >> 8) ^ x) & 0xFF]
    return c

def sum16le(d):
    return sum(int.from_bytes(d[i:i+2], 'little')
               for i in range(0, len(d) // 2 * 2, 2)) & 0xFFFF

# blocks from: python3 vbftool.py extract FILE.vbf -o bins/FILE
# ---- 14C386 -------------------------------------------------------------
assert crc16_ccitt_false(d386[:-2]) == int.from_bytes(d386[-2:], 'little')
# ---- 14C218 -------------------------------------------------------------
assert sum16le(d218) == 0xFFFF
assert int.from_bytes(d218[:2], 'little') == (0xFFFF - sum16le(d218[2:])) & 0xFFFF
# ---- 14C217 block 2 -----------------------------------------------------
assert crc16_mcrf4xx(b2[:0x73FA]) == int.from_bytes(b2[0x73FA:0x73FC], 'little')
assert sum16le(b2[:0x73FC])       == int.from_bytes(b2[0x73FC:0x73FE], 'little')
# ---- 14C217 block 1 word B (needs the matching 14C218) ------------------
img = bytearray(b'\xff' * 0x80000)
img[0x00000:0x09800] = b0
img[0x09800:0x1C000] = d218
img[0x1C000:0x80000] = b1
assert sum16le(bytes(img)[:0x7FFEC]) == int.from_bytes(b1[0x63FEC:0x63FEE], 'little')
# ---- 14C217 block 1 word A @0x0007FFEA : SOLVED (round 3) ---------------
# CRC-16/MCRF4XX over blk0 ++ blk1[START:0x63FEA), init 0xFFFF, stored LE.
# START is PER BUILD: read the long-immediate the self-check loads next to
# #$0003FFF5 (AF/AH -> #$0000E800 = blk1+0x1000; AR -> #$0000EC00 = blk1+0x1800).
START = 0x1000                      # <-- verify per version! see wordA_solved.py
assert crc16_mcrf4xx(b0 + b1[START:0x63FEA]) == \
    int.from_bytes(b1[0x63FEA:0x63FEC], 'little')
```

Verification run (2026-09-13), all assertions pass:

```
14C386  BV stored 239E = calc 239E      CV stored A629 = calc A629
14C218  BV total FFFF, word 9E34 OK     CV total FFFF, word 4309 OK
14C217  blk2 A/B: AF EFFD/35E7 OK   AH BFBA/7472 OK   AR 68A3/8655 OK
14C217  blk1 B:   AF C2AE OK            AR 3216 OK
14C217  blk1 A:   AF C704 OK   AH A291 OK   AR D110 OK   (START 1000/1000/1800)
        + live vehicle pflash dump (AR): D110 OK
```

Run `python3 work/wordA/wordA_solved.py` — it recovers `START` from each image itself and exits 0
only if all three versions match.

---

## 10. Repair order when patching (do not deviate)

**Patching 14C386 (SIGCFG)** — self-contained:
1. Recompute the trailing CRC-16/CCITT-FALSE at `block_length - 2`.
2. Container: block CRC-16, then header `file_checksum`.

**Patching 14C218 (calibration)** — self-contained, *and* it must preserve the whole-image sum:
1. Recompute the leading word at `0x00009800` so that `sum16le(block) == 0xFFFF`.
   Doing this correctly means the 14C217 block-1 word B stays valid and **the 14C217 part does
   not need reflashing**.
2. Container: block CRC-16, then header `file_checksum`.

**Patching 14C217 block 2** — self-contained:
1. word A (CRC-16/MCRF4XX) at `0x0400FFFA`.
2. word B (SUM-16) at `0x0400FFFC` — B covers A, so strictly after step 1.
3. Container: block CRC-16, then header `file_checksum`.

**Patching 14C217 block 0 or block 1** — ✅ **UNBLOCKED (round 3)**, but order is critical:
1. blk1 **word A** at `0x0007FFEA` =
   `crc16_mcrf4xx(blk0 ++ blk1[START:0x63FEA), init=0xFFFF)`, stored **little-endian**.
   ⚠ **`START` is per build — read it from the firmware, never assume `0x1000`.** It is the
   long-immediate the self-check loads next to `#$0003FFF5`
   (`AF`/`AH`: `#$0000E800` -> `blk1+0x1000`; `AR`: `#$0000EC00` -> `blk1+0x1800`).
   `work/wordA/wordA_solved.py::find_start_offset()` extracts it automatically.
   Note word A covers **blk0 too**, so *any* blk0 edit changes it.
2. (then) blk1 **word B** (SUM-16) at `0x0007FFEC`, over the full linear image including the
   paired 14C218 — strictly after step 1, since B covers A and everything below.
3. Container: block CRC-16 for each touched block, then header `file_checksum`
   (`vbftool patch` does these two automatically).

> ⚠ **The word-A check is a CONTINUOUS background monitor, not a boot-time test** (§8.0: the
> pointer walks `START -> word A`, then wraps and repeats, `0x4C00` words per pass). A wrong word A
> therefore faults the module **during normal driving**, not merely at startup — on an *electric
> power steering* ECU. Verify the recomputed word offline against
> `work/wordA/wordA_solved.py` before flashing.

---

## 11. Tooling (`work/wordA/`)

All of it is offline analysis — nothing here touches the vehicle.
**See `work/wordA/NOTES.md`** for how to read the surviving logs and why a bare `HIT` line is
not a finding.

| File | Purpose |
|---|---|
| **`wordA_solved.py`** | ⭐ **The answer.** Reference implementation of blk1 word A; recovers the per-build `START` from each image and self-verifies all three versions (exit 0 = pass). |
| **`verify_engine.py`** | Proves the asm word-form == byte-wise lo-then-hi, and controls the engine against blk2's known word A. |
| `test_compose.py` | Region **compositions** (blk0 ++ blk1 tail, gap skipped) x all 65536 seeds — **this is what found word A.** |
| `test_linear.py` | The same over the true contiguous linear image (negative, but closed the gap). |
| `decode_word.py` | **Decodes ONE word exhaustively:** every matching encoding with every field's bits, value and candidate register from each ERM table. Resolved the loop operands. |
| **`extract_encodings.py`** | **Parses the ERM's own opcode grids** (`docs/erm.txt`) into `encodings.json` — 569 encodings, 148 mnemonics, each with its erm line number. No hand transcription. |
| **`dis56800e.py`** | **Table-driven DSP56800E disassembler.** Full register tables (A-10/A-11/A-12/A-13) with load/store and word/long direction. `--selftest` runs JSR containment (55/55) **and** cross-validates against `Research/pscm_mc56f8366_disasm_v1`'s 4 hand-verified rules. |
| `dis_func.py` | Disassembles from the nearest preceding **JSR target** (a real instruction boundary). |
| `find_loop.py` | Finds every `P:`-space read and disassembles the enclosing function — how the verifier was found. |
| `find_xref.py` | References to an X: RAM cell, stores vs reads, then disassembles the writer — how `X:$11EF`'s single producer was found. |
| `find_calls.py` | Call sites **plus the `MOVE.L #imm32` arguments** — how `(START, count)` was recovered. |
| `raw_dump.py` | Raw words + every matching encoding per word, for close reading where length inference could drift. |
| `test_wordcrc.py`, `test_chunked.py` | Region/chunking variants, all seeds. Negative, but they pinned engine and feed. |
| `gen_msgsets.py` | Builds 30 candidate (region, version) message sets into `msgsets.bin`. |
| `sweep2.c` | **Main sweeper.** All 65536 polys x 4 transforms x {msb,lsb} x {LE,BE}, using the affine/XOR-difference trick so init+xorout cancel. Build: `gcc -O3 -march=native -fopenmp -o sweep2 sweep2.c` |
| `rangesweep.c` | Exhaustive `(start,end)` sweep for a fixed poly — O(n²), finds every window in one pass per start. |
| `gen_control.py` | **CONTROL** for `sweep2`: the solved blk2 word A. Must yield `poly=0x1021 core=lsb_first store=LE` uniquely. |
| `gen_single.py` | Emits `msgsets_b1.bin` (blk1, target = word A) and `msgsets_rsctl.bin` (**control** for `rangesweep`; must yield `st=0x0 end=0x73FA core=lsb store=LE`). |
| `find_crc_tables.py` | Reconstructs the 256-entry table for all 65536 polys and memcmps against every image. Proves blk0/blk1 have no CRC table. |
| `find_region_consts.py` | Scans every image for landmark addresses in **both byte and word** form. This is what found the routine. |
| `find_regions.py` | Decodes `MOVE.L #imm32,Rn` loads and reports the region triple (§8.1). |
| `dis_blk1.py` | Disassembles a blk1 window via `work/dsp56800e_dis.py`. |
| `solve_seed.py` | Recovers, by GF(2) Gaussian elimination, the init each version would need — tests the per-build-seed hypothesis exactly. |
| `sum_region.py`, `gen_hyp.py`, `gen_region.py`, `gen_cross.py` | Additive-family and region/cross-block hypothesis drivers. |

Typical use:

```bash
cd work/wordA
python3 gen_control.py && ./sweep2 msgsets_ctl.bin        # control FIRST
python3 gen_single.py  && ./rangesweep msgsets_rsctl.bin 2 0x1021   # control
OMP_NUM_THREADS=30 ./rangesweep msgsets_b1.bin 2 0x1021   # the real hunt
```

**Methodology rule established:** a sweeper's negative result counts only if that same sweeper
re-derives the known blk2 word A from its own control input. Both `sweep2` and `rangesweep` pass.
Run the control first, every time.

> ⚠ **`JSR <ABS19>` mask — get this right or your call graph is silently short.**
> Encoding is `1110 0010 0101 A1AA`: bits 3, 1, 0 are address bits 18, 17, 16 and **bit 2 is the
> literal `1`** separating JSR from JMP. The fixed-bit mask is therefore **`0xFFF4`**.
> Earlier code here (and `work/dsp56800e_dis.py`) used `0xFFF5`, which also forces bit 0 = 0 and so
> **skips `0xE255`/`0xE257` entirely**. That passed the SBL self-test at 55/55 because every SBL
> target is `0x4xxxx` (A = 100, bit 0 = 0) — a latent bug invisible on the SBL but fatal on blk1,
> where `0xE255` is the common form. With `0xFFF5` the CRC routine showed **0 call sites**; with
> `0xFFF4` it shows 2 (P-space) and 5 (X-space). **A green self-test on one image does not validate
> a mask against another.**

> `work/hunt_wordA.py` (round 1) is **superseded** — its word-wise family is subsumed by
> `sweep2`'s transforms, and that family is now excluded.

Cost note: the full 30-set x 65536-poly `sweep2` run is ~12 TB of table-CRC work and is *not*
worth repeating — the targeted region and range sweeps dominate it in value per CPU-hour.
