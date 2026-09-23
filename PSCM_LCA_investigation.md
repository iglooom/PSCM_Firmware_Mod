# PSCM LCA investigation — consolidated findings

**Question:** Lane Centering Aid (LCA) displays on the dash but applies no felt
steering torque, while Lane Keeping Aid (LKA) demonstrably works. Why, and can
it be enabled?

**Status: SOLVED — LCA STEERS (joint11, 2026-09-20).**

Driver report: *"it is definitely steering."*

Two one-word defects, both the same shape: sustained-LCA (code 5) was routed
to a degenerate dispatcher arm while LKA (code 1) and LCA-entry (code 4) got
the working one.

| Round | Patch | Defect |
|---|---|---|
| joint10 | `P:$2B02F B04E→B031` | authority increment `X:$2D4A` was **−9** (a decay term) instead of **+40** |
| joint11 | `P:$2AFAE AFE2→AFD8` | ramp increment was a **literal 0** (`E580`) instead of a computed value |

Measured outcome:

```text
                        joint9    joint10   joint11
peak |accumulator| LCA      28        54      2000
LCA / LKA                0.015     0.020      1.19
```

The demand model was confirmed exactly — `demand == (control_law * ramp) >> 10`
in **1144/1144** samples — which retrospectively validates the whole route-8
chain. The ramp now sits at its clamp in 95.6 % of code-5 samples, so the
limiter is the binding constraint again, as it should be.

Both fixes are carried in `CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF`
(sha256 `ab39234a…82a6`).

**Answered (2026-09-20):** when lane assist stops steering mid-turn, **the IPMA
stops it**, on a fixed ~3.7 s intervention timer, while the PSCM is still
granting permission. Measured on pre-patch raw CAN, so it is stock behaviour
unrelated to the LCA patches: 53 of 69 LKA episodes across five drives pile up
at 3.70–3.72 s (0.00 s spread within a drive), the PSCM held
`LaActAvail=3, LaActDeny=0` in 51 of 53, and all 65 episodes end in
`Suppr L+R` — never `Idle` — so the camera still tracks the lane and
suppresses *itself*. Not fixable in the PSCM — no change to `14C217` can
extend an intervention the camera stops requesting. **The IPMA-side constant
has since been found and is patchable there**: `tag 30A01058 +0x178 = 3.7`
seconds — see `../../IPMA/Research/IPMA_LKA_hold_time.md`.

Method, for re-derivation: from any `candump` log decode the IPMA request
(`0x0A5`, bits 30|3), PSCM availability (`0x140`, 59|2) and PSCM deny
(`0x140`, 60|1); an episode is a run of request 2/4; compare which side
changed first. Note the hands-off bit (`0x140`, 61|1) is the PSCM *reporting*
driver torque — an IPMA input, not a refusal, and not the trigger (46 of the
53 capped episodes began hands-on).

See also `fd22_joint10_fix_analysis.md`, `fd22_joint9_limiter_analysis.md`.

This document exists so the closed routes are not re-run.

---

## 1. What is established as fact

| Fact | Evidence |
|---|---|
| The IPMA sends genuine LCA steering commands | drive1 state 6, 1114 samples, `LaRefAng_No_Req` 97.8 % non-zero, −4.40…+9.20 mRad |
| LCA episodes are longer and gentler than LKA | LCA mean 7.16 s (max **57.4 s**); LKA capped ~3.8 s. LCA ~6.67 mRad vs LKA 9.32–11.40 mRad |
| The PSCM declares lane assist fully available during LCA | `LaActAvail_D_Actl = 3` for **100 %** of 6119 LCA samples; `LaActDeny = 0` throughout |
| The IPMA speed-threshold patch works | drive3: LKA engage 40.04 / drop 34.91, LCA engage 45.09 / drop 39.94 km/h — all within 0.1 km/h of target |
| The PSCM flasher works | Stock `CV6T-14C217-AR` reflashed successfully on the vehicle, twice |
| DID reads reflect the **running** application | Hand torque moved `FD0C` 00→E4 and `FD0E` 00→EA while 36 of 39 other DIDs held still |

The camera side is fully functional. The PSCM accepts the command. The gap is
between "command accepted" and "torque felt".

---

## 2. Routes closed (all negative)

### 2.1 `X:$0904` dispatcher gate — PATCHED AND DRIVEN

A CV6T-only 4-word insert in the `==6` arm of the lane-state dispatcher at
`P:$2A77E`, absent from BV6T:

```
BV6T-14C217-AF @P:$2691D     CV6T-14C217-AR @P:$2A77E
  4C06  CMP.W #6               4C06  CMP.W #6
  A203  Bne +3                 A207  Bne +7
                               F07C 0904  MOVE.W X:$0904,A   <-- inserted
                               4C01       CMP.W #1           <-- inserted
                               A203       Bne +3             <-- inserted
  E684  MOVE.W #4 (LCA)        E684  MOVE.W #4
```

Built `CV6T-14C217-AR_LCA.VBF` replacing the four inserted words with `E700`
NOPs, repaired both internal checksums, flashed, verified in flash, and drove.
The first drive produced no perceptible behavioral change.

**Live correction, 2026-09-20:** the lack of perceptible change did not prove
that the gate was already open. FD22 observation has now measured both sides:

```text
stock image, raw 0x0A5 request 6:     X:$2DDE = 0
four-NOP gate image, raw request 6:   X:$2DDE = 4 in 832/832 stable samples
```

Thus the `X:$0904` guard really did block LCA state entry. It was not the only
blocker: after bypass, `X:$2DB9` remained 0 and `X:$2D53` remained 0 throughout
state-4 episodes, explaining why the driver felt no change.

The patch remains polarity-independent. It removes the inner conditional and
falls through to `MOVE.W #4, X:$2DDE` whenever the processed request is 6.
The combined gate+FD22 image verified the runtime effect directly.

### 2.2 `X:$2DC1` consumer gate — PRESENT IN BOTH BUILDS

All four LCA consumer sites test a second cell before acting:

```
P:$2ABF7  TST.W  X:$2DC1        (non-zero)
P:$2AC76  CMP.W  #1, X:$2DC1    (exactly 1)
P:$2ACAE  CMP.W  #1, X:$2DC1
P:$2ACEA  CMP.W  #1, X:$2DC1
```

The cell has one literal write to `1` in the entire image, by an initialiser at
`P:$2AA6D` that sits in a block of thirteen zeros. The routine is reachable —
called from `P:$2A719` inside `P:$2A712`, itself called from `P:$2A75B`.
That static fact does **not** establish its live value: indirect writes, later
state-dependent ownership, or an unexecuted initialisation path remain possible.
The gate-open drive held `X:$2DDE = 4` while all four consumers failed to advance
`X:$2DB9`, making live observation of `X:$2DC1` the next required measurement.

> **Correction to an earlier claim.** This gate was first reported as
> CV6T-only. That was wrong: the search looked for `X:$2DC1` — a *CV6T*
> address — inside BV6T. Using the correct address, BV6T has all four sites
> with byte-identical structure:
> ```
> CV6T  lane state X:$2DDE,  gate X:$2DC1   (offset -0x1D)
> BV6T  lane state X:$23E4,  gate X:$23C7   (offset -0x1D)
> ```
> BV6T additionally writes the flag to 0 twice (`P:$26AE6`, `P:$26BD0`), so
> the *working* build can disable LCA at runtime while ours cannot — the
> opposite of the hypothesis.

### 2.3 Live state/code mapping supersedes the static labels

The joint FD22/raw-CAN captures establish the sustained live mapping:

```text
raw CAN 2 (LKA-left)   -> X:$2DDE = 1 -> X:$2DB9 = 1 -> accumulator nonzero
raw CAN 4 (LKA-right)  -> X:$2DDE = 2 -> X:$2DB9 = 1 -> accumulator nonzero
raw CAN 6, stock       -> X:$2DDE = 0 -> X:$2DB9 = 0 -> accumulator zero
raw CAN 6, gate open   -> X:$2DDE = 4 -> X:$2DB9 = 0 -> accumulator zero
```

Stable gate-open sample counts were 93/93 for each LKA direction and 832/832
for LCA. Code 2 appeared only transiently after active requests in this drive;
it is not the sustained active-LKA code. The earlier static assignment of LKA
to code 2 and LCA to codes 4/5 did not describe the observed running state
machine and is retracted.

A downstream function at `P:$2B0B4` does contain comparisons accepting values
2 and 5, but those comparisons cannot be treated as the complete lane-torque
enable semantics: live LKA accumulated torque while `X:$2DB9 = 1`. Its role
must be reclassified in the context of the surrounding state-machine phases.

Most importantly, the gate-open failure is now before that function:
`X:$2DB9` never advances from 0 during state-4 LCA.

### 2.4 The torque function — BYTE-IDENTICAL ACROSS BUILDS

The function containing the torque enable is **107 words in both builds** and
differs in exactly **14** words, every one an *operand* of an identical opcode:

```
 off   BV6T   CV6T   preceding opcode
 +  3  23AA   2D53   F27C  (same)     torque accumulator
 +  5  7422   B00B   E256  (same)     JSR target
 +  7  21F0   2D49   F17C  (same)     working value
 + 15  21F1   2D4A   F07C  (same)
 + 17  21F2   2D4B   4444  (same)
 + 19  21F3   2D4C   D07C  (same)
 + 38  21F0   2D49   F07C  (same)
 + 40  21F2   2D4B   D17C  (same)
 + 51  21F2   2D4B   F07C  (same)
 + 55  23AA   2D53   D07C  (same)
 + 57  23BF   2DB9   F07C  (same)     <- the per-state code cell
 + 68  23AA   2D53   F07C  (same)
 +102  23A9   2D52   E681  (same)     output
 +105  23A9   2D52   E680  (same)
```

All 14 are RAM addresses that moved in relocation, in consistent blocks
(`+0x0B59` for the working values, `+0x09A9` for accumulator and output,
`+0x09FA` for the per-state cell). The executable logic is identical.

> An earlier draft said 13. The script that produced that figure filtered out
> offset +57 — the per-state code cell `X:$23BF` → `X:$2DB9` — because its
> delta matched a relocation constant the filter was using to suppress noise.
> `work/verify_investigation.py` caught the discrepancy.

### 2.5 Live phase-1 consumer trace proves the second blocker

The format-1 joint3 capture observed the consumer state directly. Across all
1,171 matched state-4 samples:

```text
X:$2DC1 = 1
X:$2DC3 = 0
X:$2DAF = 1
X:$2DB9 = 0
X:$2D53 = 0
```

The phase-1 LCA entry arm is:

```text
P:$2ABF7  test X:$2DDE against 4
P:$2ABFB  TST.W X:$2DC1
P:$2ABFD  Bne P:$2AC07
P:$2ABFF  compare B against 3
P:$2AC01  MOVE.W #5,X:$2DAF
P:$2AC03  MOVE.W #4,X:$2DB9
```

Thus `X:$2DC1 = 1` skips the phase-5/code-4 entry writes. The live phase
remaining 1 and code remaining 0 are the exact predicted outcome. The cell's
entry semantics are therefore an **inhibit**, not a positive enable: zero
permits entry and nonzero blocks it.

Later sustained-state arms have different semantics. For example, the phase-5
arm compares `X:$2DC1` with 1 before writing phase 6/code 5. Therefore globally
forcing this cell to zero would be incorrect. A minimal experiment must bypass
only the phase-1 test/branch at `P:$2ABFB..P:$2ABFD`, retaining the following
`B == 3` condition and every sustained-state condition.

Full evidence and episode counts are in
`work/lca_resume/fd22_joint3_trace_analysis.md`.

#### Phase-1 bypass result: LCA torque path reached, feedback oscillates

The joint4 image neutralised only the phase-1 `X:$2DC1` test/branch while
retaining the following `B == 3` comparison. The live result was:

```text
request 6 -> state 4 -> phase 5/code 4 -> phase 6/code 5
```

`X:$220F` was 3 in every sampled phase-5/code-4 and phase-6/code-5 state, so
the retained `B == 3` condition passes. All 11 sampled phase-5/code-4 points
had a nonzero torque accumulator. This proves entry into the OEM LCA torque
path; no state or torque source was forced directly.

The remaining failure is a feedback loop. Forty-seven raw request-6 bursts
lasted only 0–121 ms. Across 28 clean multi-frame cycles, the median sequence
was:

```text
PSCM LaActAvail_D_Actl = 3
  49.1 ms -> IPMA request 6
  38.6 ms -> PSCM LaActAvail_D_Actl = 1
  30.4 ms -> IPMA request 7
  about 1 s -> retry
```

`LaActDeny_B_Actl` remained zero. The next failed boundary is therefore the
PSCM availability-feedback producer, not another LCA state-entry gate.

Joint5 tested the tentative `X:$2213 -> X:$226C -> X:$220F` producer mapping
and disproved it. The three cells were identical in 3,886/3,886 recovered
samples, but agreed with transmitted `LaActAvail_D_Actl` in only 2,312/3,886;
923 samples had internal value 1 while CAN availability was 3. `X:$220F`
remains the live value satisfying the retained `B == 3` state-machine
comparison, but it is not the CAN status source.

Joint6 corrected the frame-packing assumption. The `14C386` record at
`X:$4198` (`7EBB 7F74 0000 0C02 0002`) genuinely describes byte 2 / mask
`0x0C`; it does **not** describe transmitted availability. Runtime results:

```text
X:$7EBB = 0xFFFF:                         2,298 / 2,298 samples
(X:$3F5B & 0x000C) >> 2 = 3:             2,269 / 2,269 synchronized samples
candidate/CAN disagreement when CAN=1:   2,040 samples
```

Direct raw-frame decoding proves the real field location:

```text
LaActAvail_D_Actl = (frame_0x140_byte[7] >> 2) & 3
agreement: 11,449 / 11,449 raw frames
```

Frame bytes 6–7 map to final payload word `X:$3F5D`, not `X:$3F5B`.
The corrected phase-aligned record starts at `X:$419A`; its `X:$7EBB` field
is a packed-byte pointer (`2 * X:$3F5D + 1`), not a word address. Its mask
`0x0C` and shift `2` therefore do identify the high byte's availability field.
Joint6's constant `0xFFFF` came from reading the packed pointer as an X-word
address.

Static tracing localized the candidate producer:

```text
P:$2B8A2..P:$2B8CC computes X:$2D28
P:$2B8DA copies X:$2D28 -> X:$2252
P:$1DB41..P:$1DB99 composes the status frame
P:$0FF2C loads &record[+3] = X:$419D
P:$31147 updates the packed-byte field
```

`X:$2D28` is computed from two predicates:

```text
A = X:$2DB9 in {2,3,5}
B = X:$2DBA in {2,3}
X:$2D28 = 3 - 2*A - B
```

Thus live LCA code `5` explicitly selects availability `1` whenever `B=0`,
while conventional sustained LKA code `1` does not.

Joint7 confirmed the complete producer path. `X:$2D28` and mirror `X:$2252`
agreed in 1,615/1,615 samples, and every sample followed the derived truth
table. The producer matched raw frame `0x140` in 1,596/1,600 synchronized
samples; all four differences were during rapid transitions. Agreement was
exact in 1,564/1,564 samples where both internal and surrounding CAN values
were stable.

The failure mechanism is therefore proven: phase 5/code 4 retains availability
3, while phase 6/code 5 lowers it to 1, causing IPMA to withdraw request 6.
The narrow modification `P:$2B8B0 A303 -> E700` was built into
`CV6T-14C217-AR_LCA_CODE5_AVAIL_FD22.VBF` and validated in joint8. It
neutralizes only the code-5 branch while preserving code-2/code-3 suppression,
auxiliary predicate `X:$2DBA`, and the OEM control path.

Joint8 recorded 3,288/3,288 valid FD22 rows over 329.088 seconds. Phase 6/code
5 retained internal and raw-CAN availability 3 in 448/448 synchronized samples,
with nonzero torque accumulation present in 203. Request 6 became sustained:
seven substantive episodes lasted 0.825–39.066 seconds, versus 20–120 ms before
the patch. The longest episode was 39.066 seconds. Conventional request 2 and
4 positive controls remained functional; all 151 phase-2/code-1 samples had
nonzero accumulation and availability 3. CAN denial remained zero in all
16,451 frame-0x140 samples. This proves the request/availability handshake and
OEM torque-state path are sustained; subjective steering quality and actual
PSCM DTC state remain separate checks.

See `work/lca_resume/fd22_joint6_tx_source_analysis.md`,
`work/lca_resume/availability_producer_static_trace.md`,
`work/lca_resume/fd22_joint7_availability_producer_analysis.md`,
`work/lca_resume/fd22_joint8_code5_availability_test.md`, and
`work/lca_resume/fd22_joint8_code5_availability_analysis.md`.

### 2.8 Per-state limiter dispatchers — ASYMMETRY FOUND (route 8)

Two dispatchers on `X:$2DB9` select how the lane command is limited:

```text
P:$2AFA4  rate limit          -> X:$2D46
P:$2B025  authority increment -> X:$2D4A
```

Both put `{1,4}` on runtime-scheduled limits and `{2,5}` on flat constants.
Dispatcher 2 does it by shared arm; dispatcher 1 by which arms call the divider
`P:$0011F` (true for codes 1 and 4 only). `X:$2D4A` then bounds the integrator
`X:$2D4B` that produces the accumulator `X:$2D53`.

Because sustained LKA is live code **1** and sustained LCA is live code **5**,
LKA gets scheduled limiting and LCA gets flat limiting. Every earlier static
comparison used code 2 vs code 5 — the same bucket — and so could not see this.

The governing constants `X:$08F4/5/6` and `X:$03AF/$03B0` are read-only
(zero writes image-wide) in a low-X bank whose init segment is unlocated; their
values are **not yet known** and a brute-force delta search was uninformative
(~20 000 candidates).

**Cross-build (route 8b).** BV6T, which has working LCA, differs on the cell
that codes 4 and 5 *share* as their rate-limit source:

| Build | code4/5 source | Runtime writer | Verdict |
|---|---|---|---|
| BV6T-AF | `X:$23A7` | `P:$273BA`, fed by producer `P:$27346` | **DYNAMIC** |
| CV6T-AR | `X:$2D50` | none — literal `#$0400` only | **CONSTANT** |
| CV6T-AH | `X:$2891` | none — literal `#$0100` only | **CONSTANT** |

BV6T's wrapper calls the producer every cycle right before the dispatcher
(`P:$27460: JSR $27346; JSR $273BE`); CV6T's wrapper calls no producer. The
producer interpolates over runtime state (`X:$23DE`, `X:$23DA`, `X:$21EB`) plus
calibration (`X:$0601/$0602`).

The **same-family control passes**: CV6T-AH behaves like CV6T-AR, not like
BV6T, so this is a real cross-family difference rather than recompilation
noise. This supersedes §P7 of `work/lca_resume/dataflow_report.md`, which
recorded the same fact but dismissed it because it was evaluated against the
then-current (now retracted) code-2-is-LKA labelling.

Dispatcher 2 is **reversed**: BV6T's code-1/4 arm is a constant load
(`X:$05FA`), CV6T's is scheduled (`(X:$08F4 * X:$2DA0) >> 8`). So the two
builds are not one design with a feature removed — they are two independently
retuned layers, and only the dispatcher-1 source difference falls on the LCA
code.

Not proven: that BV6T's dynamic value is *larger*. It could schedule down. Full
evidence, caveats, and the joint9 plan:
`work/lca_resume/lca_authority_limiter_analysis.md`; re-derive with
`work/lca_resume/verify_authority_limiter.py` (33/33, includes the control).

### 2.9 Auto-park — the positive control that bounds the hypothesis

The module carries semi-automatic parallel parking: RX `0x170 PAM_h_FrP00`
(`ExtSteeringAngleReq`, buffer `X:$3F6E`), decoded at `P:$1E0B8` into the struct
`X:$23F2`, with a 10-entry state machine on `X:$23FC` at `P:$1E0ED`; TX `0x0B0`
carries `SAPPAngleContrError`.

Park torque greatly exceeds LKA/LCA, and the park region `P:$1E000..$1E600`
reads its own calibration bank (`X:$0535..$053B`, `$055F`, `$05A0..$05A1`) while
touching **no** `X:$2D4x/2D5x/2DBx` cell. So the EPS can deliver large torque on
command: the lane path's low authority is a calibration choice, not a hardware
limit. This does not license transplanting park constants into the lane path —
different scales, different consumers, unproven.

### 2.6 Configuration — LANE ASSIST IS ENABLED, NO LCA FLAG EXISTS

UCDS exposes 15 writable configuration items. Ours read:

```
FD15  Lane Assist enable        01   TRUE
FD13  Function enable           01000101   (LA enable = TRUE)
FD11  ANC enable                01
FD12  Straight Ahead Adaptation 00
```

There is **no separate LCA / lane-centering flag** anywhere in the list. The
only FALSE in the whole set is ANC (Active Nibble Control) — a steering-shimmy
damper, unrelated.

Unresolved caveat: `FD13`'s five decoded booleans disagree with the standalone
DIDs on 2 of 5 (Straight Ahead Adaptation and ANC), in *opposite* directions,
and `FD13` is 4 bytes for 5 flags. The bit layout is not determined.
**Do not write `FD13`.** See `PSCM_DID_map.md`.

### 2.7 Structural comparison — CV6T IS THE LARGER BUILD

```
instructions      BV6T 89 575    CV6T 102 845
CMP #6 sites      BV6T 72        CV6T 73
calibration curves BV6T 482      CV6T 691
```

"LCA was stripped from CV6T" is refuted by every size metric.

---

## 3. The measurement problem

`PSCM_does_it_steer.md` records that **every statistical proxy for "did the
PSCM apply torque" failed its own control**, twice producing false positives
that had to be retracted:

- drive1, hands-off LKA-LEFT window: follows-command 51/75 = 68 %, p = 0.0012
  — but the control "returns toward zero" scored 46/60 = 77 %, p = 0.0000 with
  **zero discriminating disagreements**. Fully explained by caster
  self-centering.
- drivemod, hands-off LCA window: 92/139 = 66 %, p = 0.0001 on 110
  discriminating cases — but the **suppressed** control scored 123/160 = 77 %.
  Root cause: the IPMA sends a live demand in *every* state (idle 99.4 %, LCA
  99.5 %, suppressed 83.4 % non-zero), so it tracks the road, not the PSCM.

Root cause of the whole difficulty: **no EPS assist-output signal exists on this
bus.** `TorsionBarTorque` on `0x140` is *driver input* — hands off 0.108 Nm vs
hands on 0.784 Nm, a 7.3× ratio.

### 3.1 The DID route — right instrument, insufficient bandwidth

`FD0E` **Torque Loop Demand** is the EPS torque command, and `FD0C`
**Q-Axis Current** is the current delivering it. Both are readable while the
application runs (confirmed: hand torque moved them, 36 of 39 other DIDs
unchanged). Names verified by binding the UCDS parameter list positionally —
8 of 8 independently-known DIDs land on their correct names — then confirmed
physically.

A first drive produced an **unjoinable** dataset: the watch tool logged
seconds-since-start while candump logs epoch. Cross-correlating the same
physical sensor across both logs gave |r| < 0.04 at every lag from −20 s to
+40 s. Fixed by logging absolute epoch (`work/vehicle/dump_dids.py`), with a
dedicated joiner (`work/vehicle/join_torque.py`) that refuses old-format files.

A second drive, deliberately split into **LCA-enabled** and **LCA-disabled**
phases by the user, joined correctly — and showed the measurement itself is
invalid:

```
                PHASE A (LCA on)     PHASE B (LCA off)
LKA-LEFT        16.7 % (n=54)         0.9 % (n=215)
LKA-RIGHT       59.1 % (n=66)        31.6 % (n=196)
LCA              8.3 % (n=1046)        —
suppr-L+R       24.4 % (n=1757)      17.7 % (n=3669)
idle                —                 7.3 % (n=1325)
```

**The positive control failed.** LKA-LEFT at 0.9 % is below idle (7.3 %), and
the *suppressed* state carries the largest excursions (max 35 vs LKA's max 2).
LKA demonstrably steers the car, so a valid torque measurement cannot show
that — the LCA figure from this run is therefore not interpretable. Cause: ~73 ms per ISO-TP sweep sampling a control loop running at
perhaps 1 kHz — isolated instants, not loop behaviour. The bench test worked
only because torque was held steady for seconds.

> `join_torque.py`'s built-in verdict initially reported "control OK" because
> it pooled both LKA directions (21.1 %) against a null mixing idle with
> `suppr-L+R`. That pooling hid the 0.9 % vs 31.6 % split. The check should be
> **per-state**.

---

## 4. What remains untested

1. **The per-state limiter constants — now the top priority.** `X:$08F4/5/6`
   (authority increment) and `X:$03AF/$03B0`, `X:$2D50` (rate limit) select
   between scheduled and flat limiting per state code. All are read-only
   calibration whose ROM→RAM init segment is not located, so they must be read
   live. Route 8, `work/lca_resume/lca_authority_limiter_analysis.md`.
2. **In-ECU logging.** The only instrument with adequate bandwidth would be a
   patched DID handler sampling `FD0E` internally. Blocked: the DID table at
   `P:$0EDC4` stores *descriptor indices* (low 12 bits step by 3 per DID), not
   addresses, and the descriptor array has not been located — the string pool
   address `P:$0F9C2` appears nowhere as a literal, and there are zero
   references to the table base. Finding the `0x22` service handler in code
   would reveal the format.
3. **The `0x0A5` checksum**, still unsolved — eliminated: all 2⁷ byte/nibble
   subset-sums, CRC-8 over all 255 polys × all inits × spans × reflections,
   and signal-wise sums. Both bytes are provably deterministic (5850 distinct
   patterns over 5875 frames, zero contradictions). Blocks synthesising `0x0A5`
   frames; does not block replay.
4. ~~**Upstream gain on `X:$2D53`** before the limiter.~~ **Addressed by route
   8.** The fifth look did find it: the limiting is not a gain on `X:$2D53` but
   a per-state bound on its integrator increment `X:$2D4A` and on the rate
   limit `X:$2D46`. The previous four looks missed it because they compared the
   two codes that share a bucket.

---

## 5. Assessment

Seven routes returned negatives; the eighth found a structural asymmetry
(§2.8). The firmware *does* contain a path that treats the live LCA code
differently from the live LKA code — but as a per-state limiter selection, not
as a gate or a missing feature, which is why gate-hunting kept returning
nothing.

Of the two readings that previously remained open:

- **LCA is applying torque, but with too little authority to hold the lane.**
  Now has a concrete candidate mechanism: flat constant rate/authority limits
  instead of scheduled ones. Matches the user's report — *"it wanders from lane
  to lane until LKA kicks"* — and matches joint8, where `X:$2D53` was nonzero
  in 203/448 phase-6 samples (torque present, evidently insufficient).
- **LCA is accepted but ineffective for a reason outside the traced paths.**
  Still possible; §2.8 is untested live.

The evidence now favours the first reading, but does not yet establish it: the
constants have not been read, so "flat" is not yet proven to mean "smaller".

---

## 6. Tools

| Tool | Status |
|---|---|
| `work/pscm_flash.py` | 44/44 selftests; **flashed stock on the vehicle twice** |
| `work/validate_against_oem.py` | 18/18; replays a real OEM flash capture |
| `work/vehicle/dump_dids.py` | all selftests; read-only (proven by socket recorder), epoch timestamps |
| `work/vehicle/verify_dids.py` | all selftests; re-extracts the DID table from the binary |
| `work/vehicle/join_torque.py` | 9/9; exact epoch join, rejects old-format logs |
| `work/disasm/flow56800e.py` | 20 tests; DSP56800E disassembler |
| `work/patch_lca_gate.py` | selftests pass; builds the gate-NOP VBF |
| `work/verify_lca_vbf.py` | 15/15; independent second code path |
| `work/lca_resume/verify_authority_limiter.py` | 33/33; route-8 structure + cross-build control |
| `work/lca_resume/build_lca_joint9_limiter_vbf.py` | selftest passes; builds the joint9 telemetry VBF |
| `work/lca_resume/verify_lca_joint9_vbf_independent.py` | READY TO FLASH; independent second code path |
| `work/lca_resume/fd22_joint9_limiter_logger.py` | selftest passes; FD22 format-6 logger |
| `work/lca_resume/analyze_joint9_limiter.py` | 6 scenarios; splits LKA/LCA out of ONE log |
| `work/lca_resume/build_lca_joint10_fix_vbf.py` | selftest passes; builds the one-word LCA fix |
| `work/lca_resume/verify_lca_joint10_vbf_independent.py` | 29/29 READY TO FLASH; independent second code path |
| `work/lca_resume/build_lca_joint11_ramp_vbf.py` | selftest passes; builds the ramp fix + format-7 telemetry |
| `work/lca_resume/verify_lca_joint11_vbf_independent.py` | 43/43 READY TO FLASH; audits BOTH dispatch tables |
| `work/lca_resume/fd22_joint11_demand_logger.py` | selftest passes; FD22 format-7 logger |
| `work/lca_resume/analyze_joint11_demand.py` | 5 scenarios; validates the demand model, not just the values |

### Next test

`work/lca_resume/fd22_joint11_ramp_test.md` — **not yet driven.**

```text
CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF
sha256 ab39234a295da0da21b9ad59c585354e8aab67bdb8858eeff561c5dfa99a82a6
two words: P:$2AFAE AFE2->AFD8 (ramp) + P:$2B02F B04E->B031 (authority)
```

Expect **more** authority than joint10. Mandatory stationary check and
explicit abort criteria in the plan.

### Branch polarity (calibrated, use this)

```
A3xx = Beq      A2xx = Bne      target = pc + 1 + offset7   (7-bit signed)
```
Calibrated on `P:$2B0B4`, the one site whose intent is unambiguous. The
encoding table has no `Bcc <OFFSET7>` entry, so `dis_at.py` prints these as
`.word` — polarity must be derived, and getting it backwards produced one of
the retracted claims above.

### Key addresses (CV6T-14C217-AR)

```
P:$2A77E   lane-state dispatcher, the ==6 arm (flash 0x054F00)
P:$2ABF7   LCA consumer, entry      -> X:$2DB9 = 4
P:$2AC76   LCA consumer, sustained  -> X:$2DB9 = 5
P:$2ACAE   LCA consumer, sustained  -> X:$2DB9 = 5
P:$2ACEA   LCA consumer, sustained  -> X:$2DB9 = 5
P:$2AC25   LKA consumer             -> X:$2DB9 = 2
P:$2B0B4   torque enable (accepts 2 and 5)
P:$2B07C   torque function, 107 words
P:$0EDC4   DID table, 40 entries, stride 6
X:$2DDE    internal lane state      X:$2DC1  consumer gate (init 1)
X:$2DB9    per-state code           X:$2D53  torque accumulator
```
