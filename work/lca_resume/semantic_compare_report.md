# BV6T-AF vs CV6T-AH/AR: semantic comparison around LCA consumers

## Result

The four LCA consumers themselves do **not** contain a BV6T-working / CV6T-broken
semantic difference. After instruction decoding, switch-table recovery, RAM
relocation mapping, and control against `CV6T-14C217-AH`, all four consumer
neighbourhoods have the same lane-state test, gate test, branch polarity, and
state/code writes.

One previously unreported difference exists immediately downstream:

```text
BV6T-AF  P:$2746F   4C68   ASRR.L #8
CV6T-AH  P:$2956F   4C68   ASRR.L #8
CV6T-AR  P:$2B065   4C6A   ASRR.L #10
```

This is in the common fixed-point multiply that produces the input to the
107-word torque function. AR therefore shifts the intermediate product two more
bits than both the presumed-working BV6T build and the same-family AH control.
Taken in isolation, that is a factor-of-four reduction before saturation.

It is **not yet valid to call this a fourfold torque-gain regression**. AR also
changes scale-like helper arguments from `0x0100` to `0x0400` in the mode/ramp
helper and adds a ×4-looking conversion in non-active switch arms.
Those correlated changes are the signature of a Q-format/unit migration, and
could compensate the extra shift. The active LKA (`code 2`) and sustained-LCA
(`code 5`) arms do not execute the newly added conversion, but their source
cells may already be in the new units. Thus the shift is a real, control-backed
candidate, not a proven root cause.

No patch was made.

## Method and anchors

The comparison did not use a raw blob diff as evidence. The tool
`semantic_compare.py`:

1. decodes real DSP56800E instruction records from all three binaries;
2. recovers the indirect switch tables as `(low16, high3)` address pairs;
3. aligns instructions after normalising branch displacements, call targets,
   and relocated X-memory operands while retaining opcodes and immediates;
4. compares `CV6T-AH -> CV6T-AR` as the same-family control before interpreting
   `BV6T-AF -> CV6T-AR` differences;
5. asserts that all four nine-instruction consumer cores are semantically equal;
6. follows direct callees and the downstream torque producer/function chain.

Recovered anchors:

| Role | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---:|---:|---:|
| state-machine function | `P:$26D18` | `P:$2912E` | `P:$2ABB5` |
| LCA consumer 1 | `P:$26D5A` | `P:$29170` | `P:$2ABF7` |
| LCA consumer 2 | `P:$26DD9` | `P:$291EF` | `P:$2AC76` |
| LCA consumer 3 | `P:$26E11` | `P:$29227` | `P:$2ACAE` |
| LCA consumer 4 | `P:$26E4D` | `P:$29263` | `P:$2ACEA` |
| common multiply / torque caller | `P:$27460` | `P:$2955E` | `P:$2B054` |
| torque function | `P:$27476` | `P:$29586` | `P:$2B07C` |
| torque function immediate callee | `P:$27422` | `P:$2952F` | `P:$2B00B` |

The relocation of the state cluster is coherent:

```text
BV6T -> AR: +0x09FA   lane $23E4->$2DDE, gate $23C7->$2DC1,
                         code $23BF->$2DB9, mode $23B5->$2DAF
AH   -> AR: +0x0508   lane $28D6->$2DDE, gate $28B9->$2DC1,
                         code $28B1->$2DB9, mode $28A7->$2DAF
```

This avoids the earlier error of searching a CV6T RAM address directly in
BV6T. The already-known enable logic (`code 2` and `code 5`) was used only as an
anchor and is not reported as a new finding.

## Four consumers and mode transitions

The state machine dispatches internal mode `X:$2DAF` over cases 0..8. In AR:

- case 1 contains entry consumer `P:$2ABF7`;
- case 5 contains sustained consumer `P:$2AC76`;
- shared cases 4/7 contain sustained consumer `P:$2ACAE`;
- case 8 contains sustained consumer `P:$2ACEA`;
- case 2 contains the LKA consumer `P:$2AC25`.

For every LCA consumer, the first nine decoded instructions are identical in
all three builds after X-address relocation. This covers the lane-state compare,
`gate` read, condition on the second live register, and arrival at the first
mode/code write. The writes and exits align as well in the full-function
comparison.

Full state-machine alignment:

| Pair | decoded instructions | semantically aligned | meaningful edits |
|---|---:|---:|---|
| AH -> AR | 264 vs 264 | 263 | one diagnostic-site constant |
| BV -> AR | 260 vs 264 | 257 | case-6 condition, case-0 idle condition, diagnostic constants |

The BV/CV differences do not occur in the cases containing the four LCA
consumers:

- CV has an additional condition in internal **case 6** involving the relocated
  cell `X:$2260` (AR). This is internal state 6, not CAN enum 6/LCA.
- CV has an additional `lane state == 0` condition in internal **case 0**.
- AH and AR are structurally identical at both sites, so neither is an AR-only
  regression.
- A constant passed immediately before a shared diagnostic-looking callee moves
  `0x5D7C` (BV) / `0x5D7E` (AH) / `0x5DEA` (AR); BV also uses `C1=0x00D0` versus
  AR `0x00CB`. This is outside the consumer decision and the callee itself is
  semantically identical.

### Ramp / timeout inputs

The LCA/LKA state code contains no differing literal timeout or ramp constant.
The same aligned compares read relocated low-X cells:

| AR | AH control | BV presumed working |
|---:|---:|---:|
| `X:$08F3` | `X:$0759` | `X:$05F9` |
| `X:$08F2` | `X:$0758` | `X:$05F8` |
| `X:$08E7` | `X:$074D` | `X:$05ED` |
| `X:$08E8` | `X:$074E` | `X:$05EE` |
| `X:$08F1` | `X:$0757` | `X:$05F7` |

Their *uses* align, but their runtime/calibration values are not embedded as
immediates at these sites. Consequently this comparison excludes a code-level
ramp/timeout difference but cannot exclude a calibration-value difference.
That limitation is consistent with the existing finding that the 14C218 blocks
cannot be globally positionally aligned.

## Immediate callees and input selection

### State-machine direct callee

The only direct call inside each recovered state-machine body is:

```text
BV6T P:$26E73 -> P:$0ECA6
AH   P:$29289 -> P:$0F618
AR   P:$2AD10 -> P:$0FACE
```

Each callee is 104 words / 90 decoded instructions, and all 90 semantic tokens
align in both comparisons. It supplies no LCA-only difference.

### Mode/ramp helper before the common multiply

The helper dispatches on the per-state code 0..5. For the torque-enabled modes,
its recovered targets are:

| Build | code 2 (LKA) | code 5 (sustained LCA) |
|---|---:|---:|
| BV6T-AF | `P:$273E6` | `P:$273F9` |
| CV6T-AH | `P:$294F3` | `P:$29506` |
| CV6T-AR | `P:$2AFCF` | `P:$2AFE2` |

The code-2 arms have the same operation sequence (select destination, set
`Y0=0`, join common ramp logic). The code-5 arms likewise copy the selected
source to the working cell, set `Y0=0`, and join the same common logic. There is
no branch selecting a weaker LCA-only input.

AR does change scale-like helper arguments from `0x0100` to `0x0400` and has an
extra multiply in switch arm 1. That extra block is not reached by code 2 or
code 5, but the correlated argument change is why the downstream `#8 -> #10`
shift cannot be interpreted as an uncompensated gain change without tracing the
source-cell units.

### Torque-function immediate callee

The immediate callee of the known torque function also dispatches over codes
0..5. In every build, **code 2 and code 5 resolve to the same target**:

```text
BV6T: code2 = code5 = P:$2744D
AH:   code2 = code5 = P:$29558
AR:   code2 = code5 = P:$2B04E
```

This is stronger than merely observing the final enable test: LKA and sustained
LCA also share the same immediate input-selection arm immediately upstream of
the torque function.

## Gain and saturation

### Control-backed difference

The common multiply core is structurally recognisable in all three builds:

```text
load factor; ASR16; load second factor; LSRR #16; NOP;
IMPYSU; IMACUS; IMPYUU; ADD; ASRR.L #N; store; call torque function
```

`N=8` in BV and AH, but `N=10` in AR. AH and AR otherwise have the same
34-instruction caller, including the same post-multiply signed clamp. This
survives the same-family control and is the main new candidate.

However, the `0x0100 -> 0x0400` helper-unit change is exactly the reciprocal
factor expected to accompany `#8 -> #10`. Therefore:

- **proven:** AR uses a different fixed-point scaling contract;
- **not proven:** the physical torque demand is four times lower;
- **proven:** the shift is common to code 2 and code 5, not LCA-only;
- **plausible consequence if not fully compensated:** the already gentler LCA
  demand can become unfelt while LKA remains noticeable.

### Saturation

AH and AR both clamp the post-multiply value between signed bounds before
storing the torque-function input; their clamp instruction sequence aligns.
BV uses a different producer decomposition (an extra pre-helper at `P:$27346`
and no identical inline clamp), so a raw comparison is misleading. No saturation
branch in AR distinguishes code 5 from code 2. The bound values ultimately come
from relocated live/calibration cells, so equality of physical limits is not
provable from these instruction bodies alone.

The 107-word torque-function body (plus final RTS) decodes to 92 instructions in
each build. All 92 semantic tokens align for BV->AR and AH->AR; remaining raw
word changes are relocated RAM/call operands. This independently reproduces the
known result rather than treating it as a new finding.

## Decoder pitfall found

The shared `flow56800e.py` currently computes long relative `E16C` branch
locations with `PC+2`. In this state machine that lands one word after the final
RTS and creates an impossible apparent tail-call recursion. Using `PC+1` makes
all 16 long exits in **each** build land exactly on the function's final RTS:

```text
BV6T -> P:$26EBE   (16/16)
AH   -> P:$292DA   (16/16)
AR   -> P:$2AD61   (16/16)
```

The comparison tool records both calculations and does not use the erroneous
`PC+2` target for function semantics. The shared decoder was not modified.

## Conclusion

No LCA-only input selector, ramp/timeout branch, saturation branch, gain branch,
or AR-only mode transition was found around the four consumers or their direct
callee. The best missed candidate is the control-backed common fixed-point shift
`#8 -> #10`, but surrounding `0x0100 -> 0x0400` changes make a Q-format migration
more likely than a simple fourfold attenuation. Resolving that ambiguity needs
one of:

1. trace the runtime units/producers of AR `X:$2D50/$2D54` versus AH
   `X:$2891/$2895` and BV `X:$23A7/$23AB`; or
2. capture the torque-function input cell at control-loop rate in each mode.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/semantic_compare.py
```

Generated evidence:

- `work/lca_resume/semantic_compare.py` — decoder/alignment/assertions;
- `work/lca_resume/semantic_compare.json` — machine-readable anchors, edits,
  active-mode dispatch targets, branch checks, and shift sites;
- `work/lca_resume/semantic_compare.txt` — human-readable raw run summary.
