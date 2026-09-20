# PSCM 14C217 — compile-invariant structural comparison (BV6T-AF vs CV6T-AR, control CV6T-AH)

Scope: structural route only (function inventory, call-graph fingerprints,
constant sets, jump tables). No dataflow tracing. Read-only on `bins/`.

Tools written (all under `work/disasm/`, all built on the validated
`flow56800e.py` decoder — 20/20 self-tests pass):

* `struct_cmp.py`        — inventory / fingerprint / unique / consts / jtables
* `struct_cmp_matrix.py` — pairwise unmatched matrix, dispatch-idiom census
* `struct_cmp_power.py`  — ablation detection-power experiment
* `struct_cmp_enum.py`   — CMP/mask immediate census (1-word forms included)
* `struct_cmp_probe.py`  — candidate follow-up with real disassembly

## 0. Method validity

Function discovery = vector-table seeds + linear sweep for `JSR`/`BSR`
encodings, then recursive descent. Applied identically to all three images, so
false-positive entries are common-mode.

Counts reproduce prior independent work (1685/1795/1868 here vs 1690/1803/1875
reported), and spot-checked functions decode into clean code, e.g.

```
sub_17958 (BV6T-AF)
  P:$17958  F87C 5812    MOVE.W  X:$5812,R0
  P:$1795A  F040 000A    MOVE.W  X:(Rn+$000A),A
  P:$1795C  4C03         CMP.W   #$03,A
  P:$1795D  E268 00ED    Bne     P:$17A4C
```

Instruction coverage 39.3 % / 42.9 % / 45.2 % of image words (BV/AH/AR). The
unreached remainder is data + code reached only through pointer tables; this is
the main sensitivity limit and is stated in §6.

## 1. Function inventory — SHAPE, not count

| metric | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| functions | 1685 | 1795 | **1868** |
| instructions | 89 575 | 97 976 | **102 845** |
| mean size | 53.16 | 54.58 | 55.06 |
| median size | 23 | 24 | 24 |
| leaf % | 41.31 | 41.95 | 41.81 |
| mean call-graph fan-out | 1.599 | 1.590 | 1.600 |
| call edges | 2694 | 2854 | 2989 |

Size-distribution shape, as % of each build's functions:

| bucket | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| ≤5 | 18.04 | 17.38 | 17.02 |
| ≤10 | 11.34 | 11.31 | 11.13 |
| ≤20 | 15.73 | 15.71 | 15.90 |
| ≤40 | 21.19 | 21.78 | 21.47 |
| ≤80 | 14.30 | 14.37 | 14.45 |
| ≤160 | 11.51 | 11.31 | 11.83 |
| ≤320 | 5.99 | 6.07 | 6.10 |
| ≤640 | 1.60 | 1.62 | 1.71 |
| >640 | 0.30 | 0.45 | 0.37 |

**Finding.** The three builds are the same shape to within ~0.7 pp in every
bucket. BV6T-AF is not shaped like a build with an extra subsystem; it is
shaped like a smaller, earlier compilation of the same program. CV6T-AR
carries **+13 270 instructions (+14.8 %)** and **+295 call edges** over
BV6T-AF. A build from which a feature had been *removed* would be smaller,
not 15 % larger.

## 2. Call-graph fingerprint matching

Fingerprint = (instruction count, conditional count, call count, return count,
loop count, mul count, logic count, move count, alu count, callee count) —
address-independent and register-allocation-independent. Greedy one-to-one
match, L1 distance normalised by size.

Unmatched into CV6T-AR:

| tolerance | BV6T-AF → AR | CV6T-AH → AR (CONTROL) | delta |
|---|---|---|---|
| 0.00 | 353/1685 (20.9 %) | 191/1795 (10.6 %) | +10.3 pp |
| 0.02 | 285/1685 (16.9 %) | 96/1795 (5.3 %) | +11.6 pp |
| 0.05 | 225/1685 (13.4 %) | 68/1795 (3.8 %) | +9.6 pp |
| 0.10 | 124/1685 (7.4 %) | 45/1795 (2.5 %) | +4.9 pp |

The BV6T excess is real but is *cross-family drift*, and the AH control is a
**same-family** pairing — the comparison is not like-for-like. The symmetric
matrix settles it:

Unmatched, row → column, tol 0.02:

| A \ B | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| **BV6T-AF** | — | 242 (14.4 %) | 285 (16.9 %) |
| **CV6T-AH** | 352 (19.6 %) | — | 96 (5.3 %) |
| **CV6T-AR** | **468 (25.1 %)** | 169 (9.0 %) | — |

**Finding.** Unmatchedness is *bidirectional and larger in the CV6T→BV6T
direction* (468 AR functions have no BV6T counterpart, vs 285 the other way).
Feature removal predicts the opposite asymmetry: BV6T-unique large and
AR-unique ≈ 0. What is observed is the signature of two divergent
compilations of a program that grew.

## 3. Build-unique functions (unmatched in BOTH other builds)

tol 0.02:

| build | unique | ≥40 insns | ≥80 insns |
|---|---|---|---|
| BV6T-AF | 227/1685 (13.5 %) | 111 | 74 |
| CV6T-AH | 39/1795 (2.2 %) | 18 | 13 |
| CV6T-AR | 154/1868 (8.2 %) | 89 | 65 |

**Caveat, stated explicitly:** the small CV6T-AH figure is partly a
trio-structure artefact — AH is unique only if it matches *neither* AR (easy
match, same family) *nor* BV6T, so AH-uniqueness is suppressed by
construction. It is not a clean control for the 227. The clean statement is
the BV6T 227 vs AR 154 comparison: both directions carry a large
build-unique population of comparable magnitude, i.e. noise, not a subsystem.

No BV6T-unique cluster was found that is (a) large, (b) call-graph-connected,
and (c) without any CV6T counterpart. The largest BV6T-unique functions
(`sub_290D9` n=1130, `sub_245CD` n=687, `sub_17958` n=526) are matched in size
and shape by AR-unique functions (`sub_22A86` n=818, `sub_162F9` n=371,
`sub_2E39A` n=331) — mutual drift.

## 4. Constant-set comparison (opcode-filtered)

| | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| distinct immediates | 3519 | 4161 | 4475 |
| immediate sites | 8776 | 9897 | 10 450 |
| distinct CMP immediates | 317 | 353 | 398 |

Set differences, with the control:

```
all immediates : BV6T-only vs AR = 2361, of which also in AH = 641 (27.1 %)
                 AH-only   vs AR = 2776, of which also in BV  = 641 (23.1 %)  <- CONTROL
CMP immediates : BV6T-only vs AR =  185, of which also in AH =   2 ( 1.1 %)
                 AH-only   vs AR =  195, of which also in BV  =   2 ( 1.0 %)  <- CONTROL
```

The "survives the control" rate is **indistinguishable from the neutral
baseline in both directions** (27.1 % vs 23.1 %; 1.1 % vs 1.0 %). Constant-set
differences here carry no feature signal.

The two CMP constants that did survive (`$8170`, `$8641`) were followed up with
real disassembly and **refuted**: they are not immediates at all but X-space
displacements in indexed addressing —

```
BV6T-AF  P:$0F7A7  CMP.W   X:(Rn+$8170),Y0     (30 sites)
CV6T-AH  P:$10136  CMP.W   X:(Rn+$8170),??     (30 sites)
CV6T-AR  0 sites   — the same buffer simply lives at a different X offset
```

This is the same class of error as the two documented false positives; it is
recorded here as caught, not claimed.

### Lane-relevant immediate census (1-word short-immediate forms included)

An earlier pass reported zeros for every lane constant. That was a **detector
bug, not evidence**: on this core small immediates are encoded *in the opcode
word* (CV6T-AR has 2370 one-word `CMP.W #imm` vs 404 two-word), so a census
that only reads extension words sees ~15 % of sites. Corrected census:

`CMP #imm` site counts:

| #imm | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| 0 | 77 | 80 | 84 |
| 1 | 1214 | 1417 | 1544 |
| 2 | 276 | 293 | 311 |
| 3 | 123 | 129 | 130 |
| 4 | 133 | 129 | 133 |
| 5 | 75 | 85 | 86 |
| **6** | **72** | **67** | **73** |
| 7 | 39 | 56 | 58 |
| 8 | 26 | 32 | 30 |
| total | 2807 | 3088 | 3287 |
| per 1k insns | 31.34 | 31.52 | 31.96 |

**The critical row:** `CMP #6` — the literal "is this LCA in Progress?" test —
occurs **72 times in BV6T-AF and 73 times in CV6T-AR**. There is no deficit.
Likewise `CMP #2` (276/293/311) and `CMP #4` (133/129/133) track build size.
Whole-histogram L1/2 distance: BV6T↔AR 0.078, **AH↔AR 0.034 (control)**,
BV6T↔AH 0.061 — ordinary drift.

Mask idiom census (`AND`/`BFTST`/`BFCLR`/`BFSET`/`BRCLR`/`BRSET` #mask):

| mask | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| 0x03 | 6 | 6 | 6 |
| 0x07 | 15 | 15 | 16 |
| 0x0F | 23 | 23 | 23 |
| 0x06 | 0 | 0 | 0 |
| 0x70 | 0 | 0 | 0 |
| 0xF0 | 0 | 0 | 0 |

Identical in all three — including `0x70` absent everywhere, consistent with
the brief's finding that the field is extracted by the generic
spec-word-`0x7004` engine rather than by inline masking. No build-specific
mask.

## 5. Jump/dispatch-table inventory

Dispatch-idiom census, normalised:

| | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| compare-chains (≥2 `CMP #`) | 41 | 45 | 48 |
| …per 1k insns | 0.46 | 0.46 | 0.47 |
| chain-length histogram | {2:33,3:5,4:1,9:2} | {2:37,3:5,4:1,9:2} | {2:39,3:5,4:1,6:1,9:2} |
| chains ≥4 arms (3-bit enum capable) | 3 | 3 | **4** |
| indirect `JMP` (register/table dispatch) | 38 | 41 | 41 |
| …per 1k insns | 0.42 | 0.42 | 0.40 |
| 2-word P-address tables in data | 1 (31 entries) | 1 (30) | 1 (30) |

**Finding.** No 8-entry dispatch table exists in any build. The multi-arm
switch population is identical to within one chain, and the only build with an
*extra* ≥4-arm chain is CV6T-AR. The single large code-pointer table is
31/30/30 entries — one entry difference, present between AH and AR too.

A candidate "5 × 7-entry addr16 tables, CV6T-AR only" was found and
**refuted**: the words are literal repeated `0x0100` constants
(P:$0E490, $0E4A5, $0E4BA, $0E4CF, $0E4E4), a data/calibration region, not
addresses. BV6T holds `E70A` repeated at the same offsets.

## 6. Sensitivity — what size of difference would this have detected?

Measured by ablation, not asserted. Procedure: delete M randomly chosen
functions of ≥40 instructions from CV6T-AH, re-match CV6T-AR → AH′ at
tol 0.02, and measure the rise in unmatched count over the M=0 baseline
(169/1868). Five trials per M:

| M deleted | unmatched rise (mean) | trials | recovery |
|---|---|---|---|
| 1 | +1.0 | 1,1,1,1,1 | 100 % |
| 3 | +2.6 | 3,3,1,3,3 | 87 % |
| 5 | +4.8 | 5,5,4,5,5 | 96 % |
| 10 | +9.4 | 9,10,9,9,10 | 94 % |
| 20 | +18.2 | 18,18,18,20,17 | 91 % |
| 40 | +36.6 | 35,36,38,38,36 | 92 % |

**Detection power ≈ 90–100 % per function for functions ≥40 instructions.** A
genuinely-absent LCA subsystem of even 3–5 mid-sized functions would surface
as a matched excess.

Limits, honestly stated:

* **Coverage 39–45 %** of image words. Code reached only through data-resident
  function-pointer tables is invisible to this method. A feature implemented
  entirely as an indirectly-dispatched task would be missed.
* **No cross-family negative control exists.** Only one BV6T build is
  available, so cross-family drift cannot be calibrated the way same-family
  drift can. §2's symmetric matrix is the mitigation, not a substitute.
* Functions **<40 instructions** are below reliable resolution — a 10-line
  `if (state == 6) return;` gate would *not* be resolvable as a shape
  difference. It would, however, have shown up in the §4 `CMP #6` census, and
  did not.
* Greedy matching is order-dependent; re-running with shuffled order moved
  counts by <2 %.

## 7. Verdict

**Negative.** By compile-invariant structural comparison, BV6T-14C217-AF shows
**no lane-assist functionality that CV6T-14C217-AR lacks.**

Every candidate difference either failed the AH-vs-AR control or was refuted by
real disassembly:

| candidate | BV6T vs AR | AH vs AR control | disposition |
|---|---|---|---|
| function count | 1685 vs 1868 | 1795 vs 1868 | scales with build age — noise |
| function shape | ≤0.7 pp per bucket | ≤0.7 pp | noise |
| unmatched fns | 285 (16.9 %) | 96 (5.3 %) | bidirectional (AR→BV 468); not a control-passing asymmetry |
| CMP-const set diff | 185, 1.1 % survive | 195, 1.0 % survive | at baseline — noise |
| `$8170` / `$8641` | 30 / 2 BV6T sites | 30 / 2 AH sites | **refuted** — X displacement, not immediate |
| `CMP #6` sites | 72 vs 73 | 67 vs 73 | no deficit |
| mask `0x70` | 0 vs 0 | 0 vs 0 | absent everywhere |
| 8-entry dispatch table | none in any build | — | does not exist |
| AR-only addr16 tables | 5 × 7 | — | **refuted** — repeated `0x0100` data |

The structural evidence points the other way from the hypothesis: CV6T-AR is
14.8 % more code, has more call edges, more compare-chains, and more functions
with no BV6T counterpart than the reverse. This corroborates the brief's
existing layer-4 finding that CV6T is a superset.

**This does not prove the LCA torque path is present and merely gated.** The
structural route cannot see inside the 55–61 % of the image it does not reach,
and cannot resolve a small gating branch. It rules out a *missing subsystem*;
the *small-gate* hypothesis remains open and is exactly what the parallel
dataflow route is positioned to answer.

**Recommendation:** do not treat any finding here as justification for
flashing. The one structural observation worth handing to the dataflow agent is
that `CMP #6` is equally abundant in both families (72/67/73 sites, enumerated
with addresses by `struct_cmp_enum.py`) — that list is a finite, concrete
candidate set for the "== 6" gate, and the dataflow trace can test which of the
73 CV6T-AR sites (if any) is reached from `X:$7ED7` / `X:$863C`.
