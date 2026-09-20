# BV6T vs CV6T PSCM — lane-assist (LKA / LCA) comparison

Question: does BV6T support **Lane Centering Aid (LCA)** while CV6T supports
only **Lane Keeping Aid (LKA)**?

**Verdict after disassembly: NOT SUPPORTED — and the code-size evidence points
the opposite way.** Every decodable layer shows CV6T doing at least as much as
BV6T, including identical handling of the field that encodes LCA.

```bash
python3 work/disasm/extraction_table.py   # per-signal bit-field extraction
python3 work/disasm/lane_consumer.py      # who reads the lane RAM cells
python3 work/disasm/struct_profile.py     # compile-independent code shape
```

## 1. The LKA/LCA field is extracted identically by both builds

The LKA/LCA distinction is **not** a separate message or signal — it is an
enum inside `LkaActvStats_D_Req` on 0x0A5 (`VAL_TABLE_ LaLkaLcaActiveState`):

```
0 LKA Idle        1 LKA Idle, LCA Suppressed
2 LKA Interv Left 3 LKA Suppr Left
4 LKA Interv Right 5 LKA Suppr Right
6 LCA in Progress  7 LKA/LCA Suppressed Both
```

Its geometry is `30|3@0+` → byte 3, shift 4, mask `0x70`. Both builds contain
the extraction record with spec word **`0x7004`**, twice, writing to two
destinations:

| | BV6T | CV6T |
|---|---|---|
| `LkaActvStats_D_Req` dest 1 | `X:$7EDF` | `X:$7ED7` |
| `LkaActvStats_D_Req` dest 2 | `X:$8628` | `X:$863C` |

Same spec, same count, same dual-destination pattern. **Neither build can be
distinguished at the field-extraction layer.**

## 2. The consumer code is symmetric (both unreachable by absolute address)

Scanning both images for absolute references to those RAM cells, filtered by
the verified absolute-addressing opcode set:

```
BV6T  X:$7EDF : 0 real refs  [57 bare value matches — coincidence, not refs]
CV6T  (all)   : 0 real refs
```

The lane cells are reached **only through pointer/indexed addressing** in both
builds, so no asymmetry exists here either.

> A first pass reported "57 references" in BV6T and 0 in CV6T — an apparent
> smoking gun. It was false: those were bare 16-bit words that merely equal
> `0x7EDF`. In 512 KB any given value occurs ~8 times by chance. Requiring the
> preceding word to be an actual absolute-addressing opcode reduces it to zero
> on both sides. *A value match is not a cross-reference.*

## 3. Code-shape comparison: CV6T is a strict SUPERSET

Raw word diffing is worthless (two builds of the *same* family differ by
74.5%), so the builds were compared by compile-independent structure: distinct
data-structure base addresses loaded into pointer registers, and distinct JSR
call targets.

| Build | Year | Pointer-base structures | Distinct functions |
|---|---|---|---|
| BV6T-14C217-AF | 2012 | 293 | 1690 |
| CV6T-14C217-AH | 2013 | 325 | 1803 |
| CV6T-14C217-AR | 2017 | 345 | 1875 |

Both metrics grow **monotonically with build age**. CV6T has 52 more data
structures and 185 more functions than BV6T — it is a strict superset in size.

This is the crux: the hypothesis requires CV6T to have **lost** a feature, but
CV6T contains strictly *more* code and *more* data structures than BV6T, and
the same-family control (AH→AR, +20 structs) shows this is ordinary version
growth, not a feature swap. Nothing was removed.

## 4. Everything else checked

| Layer | Result |
|---|---|
| `0x0A5` configured | both |
| `0x0A5` update-bit mask | `F3FF FFFF FFFF FFFF` — **bit-identical** |
| `0x0A5` second-table mask | `0200 0FFF FFFF FF0F` — **bit-identical** |
| `0x0A5` extraction specs | identical set |
| `0x140` TX lane status | both (`LaActAvail_D_Actl`, `LaActDeny_B_Actl`, `LaHandsOff_B_Actl`) |

Whole-config delta, all unrelated to lane assist: `+0x0B0` TX, `+0x420` RX,
`−0x090`, `GearRvrseActv_D_Actl` added on `0x080`.

## 5. What this does and does not prove

**Does:** no difference exists in message handling, signal extraction, signal
storage, or overall code volume. A feature *removal* in CV6T is inconsistent
with the size evidence.

**Does not:** the *behaviour* of the lane controller is still undecoded. The
consumers are reached through pointer chains that the current recursive
disassembler cannot resolve without data-flow analysis, so the arithmetic that
acts on `LkaActvStats_D_Req` has not been read in either build. A difference
confined to, say, a comparison constant or a torque limit would not show up in
any metric used here.

**Most likely explanation** (untested): if the vehicles genuinely behave
differently, the cause is more plausibly the **14C218 calibration** (76 KB,
undecoded — LCA needs sustained centering torque, LKA brief nudges) or the
**IPMA** camera module that originates 0x0A5, rather than the PSCM code.

## 6. Next steps, in order of value

1. **14C218 calibration** — locate the lane-assist parameter block and compare
   intervention limits, ramp rates, curvature ceilings between builds.
2. **Data-flow tracing** — extend the disassembler to follow pointer-register
   provenance so the 0x0A5 consumer can actually be read.
3. **Vehicle test** (definitive) — log 0x0A5 and 0x140; check whether the PSCM
   asserts `LaActAvail_D_Actl` when the IPMA sends `LkaActvStats_D_Req = 6`.
