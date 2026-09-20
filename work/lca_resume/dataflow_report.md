# CV6T-14C217-AR: LCA state/per-state dataflow to `X:$2D53`

Analysis date: 2026-09-15. Static read-only analysis of the extracted `14C217` blocks only; VBF/bin files were not modified.

## Brief result

A **single new candidate** was found that cannot be dismissed as “LKA and LCA are completely identical”:

* in CV6T-AR, function `P:$2B0E8` transforms internal lane state `X:$2DDE` into sign/selector `X:$2D5E`: state 1 → `-1`, state 2 → `+1`, all other states, including sustained LCA state 4, → `0`;
* the value does indeed have dataflow to the input of the `X:$2D53` computation: `2D5E → 2D5F → lookup/interpolation block → 2D94 → 2D4F → 2D55 → 2D54 → 2D49 → 2D53`;
* this exact selector shape is absent from both CV6T-AH and BV6T-AF. Thus, it is neither cross-family relocation/noise nor a difference refuted by the same-family control.

But this is **not proven torque zeroing**. Zero is supplied as the argument/center for several lookup/interpolation operations rather than being written directly to `X:$2D53`. It has not been proven that a lookup with a zero argument produces zero output or that this branch is the sole demand term.

At the same time, two explicit per-state (`X:$2DB9`) branches before the accumulator were checked and **do not produce CV6T-specific attenuation**:

1. the first branch (ramp/limiter target) for code 2 and code 5 in both CV6T builds leads to the same numerical target;
2. the second branch sends code 2 and code 5 to literally the same target;
3. the final block accepts both 2 and 5 before writing the accumulator.

---

## Reproduction

From the research root:

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/disasm/flow56800e.py --selftest
python3 work/lca_resume/trace_dataflow.py --selftest | tee work/lca_resume/trace_output.txt
python3 -c "print('0x0400 / 2^10 =',0x400/(2**10)); print('0x0100 / 2^8 =',0x100/(2**8))"
sha256sum \
  bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin \
  bins/CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin \
  bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin
```

Execution artifacts are saved alongside it:

* `trace_output.txt` — all raw windows, jump-table targets, and selftest;
* `disasm_selftest.txt` — 22 PASS lines;
* `scale_check.txt` — both scale pairs produce 1.0;
* `input_sha256.txt` — input identifiers.

Input SHA-256 hashes:

```text
CV6T-AR  6e60963a3583993d1b872ebd88bb9b2f1acdeb397a7954a1ae75be3ce7d933b0
CV6T-AH  1b9e232ac420ef492f454346cae71dc7795b22377a3cb40a32b6b1cd42d29af0
BV6T-AF  f71464911868f697caed4024b56aff78849f7c83a4226e1f2f81c2e9cd3a3e13
```

The addresses below are P-space word addresses. Raw words are shown in the order in which the already verified local tooling loads them (`struct '<H'` for the extracted bin).

> Important: the current table-driven disassembler prints `8654` operands in reverse order. The context of the contiguous initializer block shows the actual compiler layout as `8654 <X-destination> <immediate>`: for example, `8654 2D50 0400` is located between the zero-inits of `X:$2D4F` and `X:$2D51`. The conclusions below use this raw/layout fact for `8654`, not the erroneous listing line.

---

# PROVEN

## P1. New CV6T-AR-only state selector zeroes the argument for LCA state 4

CV6T-AR `P:$2B0E8`:

```text
F07C 2DDE        read X:$2DDE
4C01 A203        if state != 1, go to next arm
E6FF 2D5E        X:$2D5E = -1
A907
4C02 A203        if state != 2, go to default
E681 2D5E        X:$2D5E = +1
A902
E680 2D5E        X:$2D5E = 0
```

Given the already established mapping `LKA → state 1/2`, `LCA → state 4`, state 4 takes the default path and receives `X:$2D5E=0`.

Check for the exact selector shape (`F07C <state>; CMP #1; -1; CMP #2; +1; else 0`) across the entire blk1:

| build | selector hits |
|---|---|
| CV6T-AR | `P:$2B0E8`, `X:$2DDE → X:$2D5E` |
| CV6T-AH | none |
| BV6T-AF | none |

This claim is limited to the exact opcode shape. It proves the presence of an AR-only branch, but not the absence of any semantically equivalent logic compiled differently.

## P2. The selector has actual downstream dataflow to `X:$2D53`

Direct CV6T-AR raw anchors:

```text
P:$2B107  F07C 2D5E 6C11 8016 D07C 2D5F
           read 2D5E; multiply; store 2D5F

P:$2B110  874A 03B7 F77C 2D5F 874B 2D60 874C 2D61 E587
           first lookup/interpolation use of 2D5F
# Similar reads of X:$2D5F recur at P:$2B13E, $2B16C,
# $2B19A, $2B1C8 (and later in this lookup family).

P:$2B305  F07C 2D62 8110 F07C 2D66 4CB0 4C30 E700
           consumes lookup outputs
P:$2B366  D07C 2D94
           stores combined/limited result to 2D94

P:$2AE0F  E256 ADE9 F07C 036B 8110 F07C 2D94 4CB0 4C30 E700 7317 7397
P:$2AE45  D07C 2D4F
           2D94 participates in computation of 2D4F

P:$2AEC2  E256 AED4 E256 AEF4 E256 AE48 E256 AE0F F17C 2D4E ...
P:$2AED1  D07C 2D55
           aggregate stores 2D55

P:$2AF2D  827B D23F 887B F07C 2D55 7C00 4C61 F57C ...
P:$2AF8B  D57C 2D54
           computation from 2D55 stores 2D54

P:$2B054  ... F07C 2D54 ... F07C 2D47 ... 7217 7297 7017 7850
           4C6A ... D57C 2D49 E256 B07C ...
           combines 2D54 with ramp/limiter value 2D47; stores 2D49;
           calls torque function P:$2B07C

P:$2B0B2  D07C 2D53
           final accumulator store
```

Therefore, `X:$2D5E=0` is not a dead state flag: it enters the chain leading to the accumulator. However, there are lookup/interpolation operations, sums, and limiters between `2D5E` and `2D53`; the equality `2D5E=0` **does not imply** `2D53=0`.

## P3. First per-state branch: CV6T code 2 and code 5 produce the same target

Jump tables:

| build | dispatch/table | cases 0..5 |
|---|---|---|
| CV6T-AR | `P:$2AF90 / $2AFA4` | `2AFE7 2AFB0 2AFCF 2AFD4 2AFD8 2AFE2` |
| CV6T-AH | `P:$294CB / $294DD` | `2950B 294E9 294F3 294F8 294FC 29506` |
| BV6T-AF | `P:$273BE / $273D0` | `273FE 273DC 273E6 273EB 273EF 273F9` |

Code 2 versus code 5:

```text
CV6T-AR code2 P:$2AFCF  8654 2D46 0400 E580 A916
CV6T-AR code5 P:$2AFE2  F67C 2D50 2D46 E580 A903
CV6T-AR init  P:$2B513  8654 2D50 0400

CV6T-AH code2 P:$294F3  8654 2887 0100 E580 A916
CV6T-AH code5 P:$29506  F67C 2891 2887 E580 A903
CV6T-AH init  P:$2963D  8654 2891 0100
```

The opcode-filtered direct-reference scan for `X:$2D50`/`X:$2891` finds only this constant init and reads as a copy source (case 4 and case 5). Therefore, across all direct writes found:

* AR: code2 writes target `0x0400`; code5 copies `X:$2D50 = 0x0400` there;
* AH: code2 writes target `0x0100`; code5 copies `X:$2891 = 0x0100` there.

That is, in both CV control builds, code 2 and code 5 are numerically identical at this ramp/limiter branch.

## P4. AR `0x0400 / shift 10` is a scaling change, controlled by AH

Wrapper scale pairs:

```text
CV6T-AR P:$2B065  4C6A   shift 10; target 0x0400
CV6T-AH P:$2956F  4C68   shift  8; target 0x0100
BV6T-AF P:$2746F  4C68   shift  8; code2 target 0x0100
```

Check:

```text
0x0400 / 2^10 = 1.0
0x0100 / 2^8  = 1.0
```

Thus, the AR-only 4-fold increase in target is accompanied by an increase in right shift by 2. `0x0400` alone cannot be treated as a 4× gain; the matched pair preserves the scale. This is the same-family AR/AH control.

## P5. The second per-state branch merges code 2 and code 5 at a common target

| build | table | code2 target | code5 target |
|---|---|---:|---:|
| CV6T-AR | `P:$2B025` | `P:$2B04E` | `P:$2B04E` |
| CV6T-AH | `P:$29549` | `P:$29558` | `P:$29558` |
| BV6T-AF | `P:$2743E` | `P:$2744D` | `P:$2744D` |

Raw targets:

```text
AR P:$2B04E  A902 F17C 08F5 D17C 2D4A E708 ...
AH P:$29558  A902 F17C 075B D17C 288B E708 ...
BV P:$2744D  A902 F17C 05FB 7D10 4D66 E411 4DD3 1062 ...
```

In each build, entries 2 and 5 point to the same P-address; therefore, within this branch, LKA code 2 and sustained-LCA code 5 are indistinguishable.

## P6. The final accumulator block accepts code 2 and code 5 in all three builds

```text
AR P:$2B0B4  F07C 2DB9 4C02 A303 E700 4C05 A203 E700 E581 A901 E580
AH P:$295BE  F07C 28B1 4C02 A303 E700 4C05 A203 E700 E581 A901 E580
BV P:$274AE  F07C 23BF 4C02 A303 E700 4C05 A203 E700 E581 A901 E580
```

Accumulator stores:

```text
AR P:$2B0B2  D07C 2D53
AH P:$295BC  D07C 2894
BV P:$274AC  D07C 23AA
```

This confirms the previous conclusion using two independent controls and completes the explicit per-state path to `X:$2D53`.

## P7. Another controlled cross-family difference: BV has a dynamic code-5 ramp source

> **Superseded 2026-09-20 — this finding was right and its dismissal was wrong.**
> The closing sentence below ("its direction does not support the simple
> hypothesis") rested on the then-current labelling in which LKA was code 2 and
> LCA was codes 4/5, so comparing the CV constant against the *code2* immediate
> looked like a match. The live FD22 captures later retracted that mapping:
> sustained LKA is code **1** and sustained LCA is code **5**, and codes 4/5
> share this source cell. Against the corrected mapping, BV's dynamic source
> feeds the LCA code while CV's constant does not — which is exactly the
> asymmetry. See `lca_authority_limiter_analysis.md` §7 and
> `verify_authority_limiter.py` (33/33, same-family control included).
> The paragraph below is retained unedited for provenance.

```text
BV code2  P:$273E6  8654 21ED 0100
BV code5  P:$273F9  F67C 23A7 21ED
BV writer P:$273BA  D07C 23A7
BV init   P:$277CE  E680 23A7
BV wrapper P:$27460 begins with E256 7346 E256 73BE ...
```

`P:$27346..$273BD` computes `X:$23A7`, and the wrapper calls the producer before the per-state dispatcher. Both CV builds, by contrast, use a constant source (`AR 2D50=0400`, `AH 2891=0100`) and have no direct runtime writer for this source cell.

Control:

| property | BV6T-AF | CV6T-AH | CV6T-AR |
|---|---|---|---|
| code5 source | dynamic `X:$23A7` | constant `X:$2891` | constant `X:$2D50` |
| runtime direct writer | `P:$273BA` | none found | none found |
| wrapper calls producer | `P:$27460 → $27346` | no | no |

This is a genuine family difference. But its direction does not support the simple hypothesis “CV LCA is zeroed”: the CV constants are positive and equal to the corresponding code2 target in their respective fixed-point scale.

---

# REFUTED

1. **“In CV6T, code5 selects a smaller/zero ramp target than code2.”** Refuted for AR and AH: the direct constant source equals the code2 immediate.
2. **“AR `0x0400` produces a separate 4× gain/limit.”** Refuted by the paired shift change `8 → 10`; the normalized scale is identical.
3. **“The second per-state table contains a separate LCA gain/zero arm.”** Refuted: entries 2 and 5 are literally identical in all three builds.
4. **“The final accumulator enable disables code5.”** Refuted by the byte-identical compare chain in all three builds.
5. **“A direct LCA write of zero to `X:$2D53` was found.”** Not found. The AR-only zero is written to `X:$2D5E`, not to the accumulator.

---

# OPEN

1. **Main candidate: what the lookup tables indexed by `X:$2D5F` mean.** The tables beginning with selector arguments `0x03B7`, `0x03CC`, `0x03E1`, `0x03F6`, `0x040B` and calls to `P:$2256F` need to be decoded. It is not yet known whether index 0 produces zero, central, or nonzero coefficients.
2. **Whether the AR-only state selector is LCA-specific suppression or a new LKA directional correction.** The `-1/+1/0` mapping naturally resembles a left/right directional term, where LCA=0 means “no directional bias,” not “no torque.” Static analysis cannot yet distinguish between these meanings.
3. **Full arithmetic from lookup outputs to `2D54`.** The reachability chain is proven, but the contribution graph contains several sums/limiters; it has not been proven that the state-derived contribution dominates or is a multiplicative gain for the entire demand.
4. **Indirect writes.** The direct absolute-reference scan found no runtime writes to the CV source cells `2D50/2891`; a table/pointer-based indirect write remains theoretically possible.
5. **BV dynamic source semantics/range.** `P:$27346` statically writes `X:$23A7`, but its runtime range and the possibility of zero have not been established. This is an LCA-specific ramp difference, not a proven torque gain.

## Final classification

* **Proven:** CV6T-AR has a state-4→0 selector with dataflow to an upstream accumulator input; the exact selector shape is absent from AH and BV.
* **Open:** whether this zero applies only to directional correction or represents actual LCA authority suppression.
* **Refuted:** the two explicit `X:$2DB9` per-state branches and the final enable contain no separate CV LCA gain/zero/limit; the AR fixed-point scale change is not a gain change.
