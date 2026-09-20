# PSCM 14C218 calibration — BV6T vs CV6T

Follow-up to `PSCM_BV6T_vs_CV6T_lane_assist.md` step 1: compare the calibration
blocks for lane-assist tuning differences (LCA needs sustained centering
torque, LKA brief nudges).

**Result: the comparison is NOT POSSIBLE with the available evidence.** The two
calibrations share no usable coordinate system. This is a negative result about
*method*, not a finding about the feature.

```bash
python3 work/disasm/cal_structure.py   # curve inventory per build
python3 work/disasm/cal_align.py       # anchor-based alignment attempt
```

## 1. Block placement (new, and useful in its own right)

`14C218` loads at VBF byte `0x00009800` → **P:$04C00 … P:$0DFFF** (37 888
words). That fills the gap between `14C217` blk0 (code, `P:$00000–$04BFF`) and
blk1 (code, `P:$0E000+`), completing the P-space map:

| Region | Words | Content |
|---|---|---|
| `P:$00000–$04BFF` | 19 456 | 14C217 blk0 — code |
| **`P:$04C00–$0DFFF`** | **37 888** | **14C218 — calibration** |
| `P:$0E000–$3FFFF` | 204 800 | 14C217 blk1 — code |

This also explains the word-A CRC's "gapped concatenation" (`blk0 ++ tail of
blk1`, skipping the calibration) documented in `PSCM_internal_checksums.md`.

## 2. Why the two calibrations cannot be diffed

| Measure | Value |
|---|---|
| Words differing (positional) | 96.3 % |
| Best uniform shift | 5.4 % agreement — **noise** |
| Value-level multiset overlap | 54.6 % |
| **Positionally alignable** | **1.9 %** |

The blocks are *not* a common layout with retuned numbers; they are
independently generated images. Identical tables do survive — 22 monotonic
curves match byte-for-byte — but they sit at **piecewise-constant offsets**
(`+0xA0`, `+0x90`, `−0x3770`), so only small islands can be paired.

Anchor-based alignment (pair curves whose value sequence is unique in both
builds, grow outward while words agree) yields:

```
6 aligned regions, 732 words = 1.9 % of the calibration
   BV6T P:$04D70 <-> CV6T P:$04E00  shift  +144   241 words identical
   BV6T P:$04EC0 <-> CV6T P:$04F60  shift  +160   241 words identical
   BV6T P:$04C39 <-> CV6T P:$04C39  shift    +0   107 words identical
   BV6T P:$0B187 <-> CV6T P:$07A17  shift -14192   38 words identical
```

**Sensitivity control:** relaxing the anchor threshold by 2.5× (minimum curve
length 6 → 4, anchors 15 → 38) moves coverage only 1.9 % → 2.3 %. The ceiling
is a property of the data, not of the threshold — so the 98 % unaligned
remainder is genuinely unrelatable, not an artefact of a conservative setting.

**In the unaligned 98 %, relocation and retuning are indistinguishable.** Any
"difference" reported there would be unfalsifiable. No parameter claim is made.

## 3. What the curve inventory does show

| | BV6T-14C218-AF | CV6T-14C218-AX |
|---|---|---|
| monotonic curves (≥6 words) | 482 | 691 |
| words in curves | 3 781 (10.0 %) | 5 255 (13.9 %) |

CV6T contains **~43 % more calibration curves** than BV6T. Combined with the
14C217 code-shape result (CV6T has 52 more data structures and 185 more
functions), every size metric points the same way: **CV6T is the larger,
more-capable build.** A feature *removed* in CV6T remains unsupported by any
measurement taken so far.

That is suggestive, not conclusive: more curves could equally be finer
resolution of existing maps rather than new features.

## 4. Status of the LKA/LCA question

| Layer | Result |
|---|---|
| CAN message config (14C386) | identical for lane assist |
| Signal extraction, incl. `LkaActvStats_D_Req` | identical (spec `0x7004`, ×2) |
| Lane RAM consumers | symmetric; pointer-only in both |
| 14C217 code shape | CV6T strictly larger |
| **14C218 calibration** | **not comparable (1.9 % alignable)** |

Four layers examined, no difference found; the fifth is not decidable offline
with current tooling.

## 5. Use-site anchoring — a method that DOES work (partially)

Addresses can't be compared, but a parameter's *meaning* is fixed by the code
that reads it. On DSP56800E, program memory is read **only** through
register-indirect addressing (`MOVE.W P:<ea_m>,GGG` = `1000 0GGG 0110 1mRR`,
ERM line 26953) — there is no absolute P: read. So every calibration access is:

```
moveu.w #$<cal_addr>,Rn      ; pointer load
...
move.w  P:(Rn)+,<reg>        ; the actual read
```

Scanning for pointer loads into `P:$04C00–$0DFFF` that have a P-memory read
within 24 words (`work/disasm/cal_usesites.py`):

| | BV6T | CV6T |
|---|---|---|
| calibration pointer loads | 1 143 | 1 319 |
| …with a nearby P-read | 23 | 26 |
| functions reading calibration | 16 | 18 |

**Five parameters are read from the identical address in both builds** and are
therefore the same parameter by construction: `$04F80`, `$08C98`, `$08CF8`,
`$0D400`, `$0DFF3`.

### Method validation (ground truth)

`P:$0DFF3` decodes as byte-swapped ASCII:

```
BV6T: 'BV6T-14C218-AF'
CV6T: 'CV6T-14C218-AX'
```

— the exact part numbers. An anchor recovered purely from code-reference
structure landing on the self-identifying string proves the pairing is real,
not coincidence.

### The five anchored parameters (all differ)

```
P:$04F80  BV6T: 2B61 2C84 2DA7 0000 ...   CV6T: 7530 3C00 3C00 0000 0280 00D5 ...
P:$08C98  BV6T: 0231 0231 0231 024C ...   CV6T: 2F8E 1BC3 4033 198E 537F D433 ...
P:$08CF8  BV6T: 03E8 0005 0000 0000 ...   CV6T: 001A 0026 0026 0026 0026 0000 ...
P:$0D400  BV6T: 1983 0407 0004 0082 ...   CV6T: 6856 040A 0206 00FF 0A45 0006 ...
P:$0DFF3  part number string (above)
```

All five differ, and `$08C98`'s BV6T side is an ordered ramp (`0231 0231 0231
024C 0299 …`) where CV6T's is high-entropy — so these are not merely rescaled
copies. But **which** parameter each is remains unknown: naming them requires
tracing the reading function's purpose, and none of the five sits in a
lane-assist consumer. They are not the LKA/LCA answer.

## 6. What would still work

1. **Vehicle test (definitive).** Log `0x0A5` and `0x140`; check whether the
   PSCM asserts `LaActAvail_D_Actl` when the IPMA sends
   `LkaActvStats_D_Req = 6` ("LCA in Progress"). This answers the question
   directly and needs no further reverse engineering.
2. **Extend use-site anchoring.** The 5 shared anchors came from the ~24
   pointer loads with a P-read inside a 24-word window. Widening that window,
   following pointers through register copies, and matching functions by call
   structure (rather than requiring an identical address) should pair far more
   parameters — and would let the lane-assist maps be found by name.
3. **Check the IPMA.** `0x0A5` originates there; LCA availability may be a
   camera-module property rather than a PSCM one.
