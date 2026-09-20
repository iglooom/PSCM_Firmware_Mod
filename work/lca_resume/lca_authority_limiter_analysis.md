# LCA authority limiting — two per-state limiter dispatchers

Follow-up to `fd22_joint8_code5_availability_analysis.md`. Joint8 proved the
request/availability handshake is now sustained (request 6 held 39 s, phase
6/code 5, availability 3 in 448/448 samples), but the driver reports no felt
assistance. This document answers: **is there additional logic that limits
torque specifically in LCA mode?**

**Answer: yes.** Two separate per-state dispatchers place the live sustained
LCA code on *flat constant* limits while live sustained LKA gets *dynamically
scheduled* limits. This was missed before because earlier static work compared
**code 2 vs code 5**, and the live captures later retracted those labels —
sustained LKA actually runs **code 1**, which no static comparison had examined.

Evidence level: 3 (confirmed control flow) for the dispatch structure, level 4
(inferred semantics) for the limiter roles. Not yet measured live.

---

## 1. The two dispatchers

Both switch on `X:$2DB9`, the per-state code, and both are reached once per
lane cyclic callback via `P:$2B054 -> P:$2AF90` and `P:$2B07C -> P:$2B00B`.

### Dispatcher 1 — rate limit, table at `P:$2AFA4`

```text
code 0 -> P:$2AFE7     code 3 -> P:$2AFD4
code 1 -> P:$2AFB0     code 4 -> P:$2AFD8
code 2 -> P:$2AFCF     code 5 -> P:$2AFE2
```

### Dispatcher 2 — authority increment, table at `P:$2B025`

```text
code 0 -> P:$2B04F     code 3 -> P:$2B04F
code 1 -> P:$2B031     code 4 -> P:$2B031
code 2 -> P:$2B04E     code 5 -> P:$2B04E
```

Both tables partition the codes the same way — `{1,4}` scheduled, `{2,5}` flat,
`{0,3}` off/idle — but they express it differently, and the difference matters:

> **MEASURED 2026-09-20 (joint9) — read this before acting on §1–§7.**
> The live drive confirmed the dispatcher-2 mechanism and **refuted** the
> dispatcher-1 half of this document's framing:
>
> * **Dispatcher 2 is the cause, and worse than predicted.** `X:$2D4A` is
>   `+40` under sustained LKA (code 1) but **`−9`** under sustained LCA
>   (code 5). It is not a smaller cap, it is a **decay term** that bleeds the
>   integrator to zero. LCA's accumulator never exceeded 28 vs LKA's 1923.
> * **Dispatcher 1 is numerically INERT.** `rate_limit` measured `1024` in
>   *both* modes. `X:$2DA0` is a constant 256 (Q8 unity), so the "scheduled"
>   code-1 arm computes exactly the same number the flat arm supplies. The
>   scheduled-vs-flat split is real in code and makes no difference in value.
> * Constants resolved at last: `X:$08F4=40`, `X:$08F5=−33`, `X:$08F6=−9`,
>   `X:$2D50=1024` (constant, as predicted).
>
> Full results: `fd22_joint9_limiter_analysis.md`. The §1 claim that both
> dispatchers express one LCA-limiting design is **retracted** — only
> dispatcher 2 does.

* **Dispatcher 2** partitions by *shared arm*: codes 1 and 4 literally jump to
  the same address, as do codes 2 and 5.
* **Dispatcher 1** gives every code its own arm address. Its partition is
  *semantic*: the code-1 and code-4 arms invoke the 16-step divider `P:$0011F`,
  the code-2 and code-5 arms do not.

Verified per code by `verify_authority_limiter.py`:

```text
divider called:  {0: False, 1: True, 2: False, 3: False, 4: True, 5: False}
```

> **Correction.** A first draft of this document claimed dispatcher 1 also
> shares arms between codes 1 and 4. That is false — `P:$2AFB0` and `P:$2AFD8`
> are distinct — and the verifier caught it. The shared-arm property belongs to
> dispatcher 2 only; dispatcher 1 is tested by the divider-call property
> instead. Two dispatchers reaching the same partition by *different* means is
> stronger evidence than a coincidence of jump targets would have been.

---

## 2. What each arm computes

### Dispatcher 2 preamble, `P:$2B00B`

```text
P:$2B00B  FF7C 2DDC   TST.W X:$2DDC
P:$2B00D  A203        Bne  -> P:$2B011
P:$2B00E  F17C 08F6   B = X:$08F6
P:$2B010  A902        BRA  -> P:$2B013
P:$2B011  F17C 08F5   B = X:$08F5
```

So `X:$2DDC != 0 -> B = X:$08F5`, `X:$2DDC == 0 -> B = X:$08F6`.

### The arms

| Arm | Codes | `X:$2D4A` receives |
|---|---|---|
| `P:$2B031` | **1, 4** | `clamp( (X:$08F4 * X:$2DA0) >> 8 , X:$2DA6 )`, also stored to `X:$2DA5` — **runtime-scheduled** |
| `P:$2B04E` | **2, 5** | the preamble constant `X:$08F5` / `X:$08F6` — **flat** |
| `P:$2B04F` | 0, 3 | `X:$08F5` forced — flat |

All three converge on the single store:

```text
P:$2B051  D17C 2D4A   MOVE.W B1,X:$2D4A
P:$2B053  E708        RTS
```

Dispatcher 1 has the same shape into `X:$2D46`: the code-1 arm at `P:$2AFB0`
computes `(X:$03AF * X:$2D9A) >> 8` into `X:$2DA4`, floors it at 1, then calls
the divider `P:$0011F` — note it is the only arm that reads a lane *runtime*
cell (`X:$2D9A`). The code-4 arm at `P:$2AFD8` also calls the divider but with
the constant `X:$03B0`. The code-5 arm at `P:$2AFE2` merely copies the constant
`X:$2D50` into `X:$2D46` and branches out.

```text
code 1  P:$2AFB0  F07C 03AF ... F07C 2D9A ... D07C 2DA4 ... E254 011F   <- scheduled
code 5  P:$2AFE2  F67C 2D50 2D46 E580 A903                              <- flat copy
```

`X:$08F4/$08F5/$08F6`, `X:$03AF/$03B0` are all **read-only** in the whole image
(zero writes) — calibration, not state.

---

## 3. Why these two cells are the torque authority

`X:$2D46` is the step bound of a textbook rate limiter at `P:$2AFEA`: it forms
`target - previous`, clamps the delta into `±X:$2D46` (negative bound `X:$2D48`),
and writes the running output `X:$2D47`. A small `X:$2D46` means the command
can only *ramp* slowly.

`X:$2D4A` bounds the integrator inside the torque function `P:$2B07C`:

```text
P:$2B08A  F07C 2D4A      A = X:$2D4A
P:$2B08C  4444 2D4B      A = A + X:$2D4B
P:$2B08E  D07C 2D4C      X:$2D4C = A
P:$2B09B  4CC0 7FFE      CMP.W #$7FFE,B      ; saturation
P:$2B0A3  D17C 2D4B      X:$2D4B = B1        ; integrator state
P:$2B0AE  F07C 2D4B  6C11  8016  D07C 2D53   ; X:$2D53 = f(X:$2D4B)
```

`X:$2D53` is the accumulator already established as the torque output in
`PSCM_LCA_investigation.md` §2.4. So `X:$2D4A` is the per-cycle authority
increment feeding it.

**Net effect:** in sustained LCA (code 5) both the slew rate and the authority
increment are fixed calibration scalars, while sustained LKA (code 1) gets both
scheduled against runtime signals (`X:$2D9A`, `X:$2DA0`) with their own clamps.
This is consistent with the driver's report of a sustained-but-weak LCA that
"wanders from lane to lane until LKA kicks" — LKA's brief nudge is allowed to
scale up; LCA's steady command is not.

---

## 4. The auto-park positive control

The vehicle owner's constraint — this PSCM must carry auto-park calibration,
whose torque is far larger than LKA/LCA — is a useful falsifier, and it holds:

* `0x170 PAM_h_FrP00` is an RX frame carrying `ExtSteeringAngleReq`
  (`X:$3F6E`), decoded at `P:$1E0B8` into the struct at `X:$23F2`.
* `0x0B0 PSCM_h_FrP00` transmits **`SAPPAngleContrError`** — SAPP =
  Semi-Automatic Parallel Parking. Both frames are present in `14C386-AB`.
* The park state machine is a 10-entry dispatch on `X:$23FC` at `P:$1E0ED`.

Critically, the park region `P:$1E000..$1E600` reads its own calibration bank
(`X:$0535..$053B`, `X:$055F`, `X:$05A0..$05A1`) and touches **no** `X:$2D4x`,
`X:$2D5x`, or `X:$2DBx` cell. So park is an independent high-authority channel.

This proves the hardware and firmware can deliver large torque; the lane path's
low authority is a per-mode calibration choice, not a hardware ceiling. It does
**not** mean the park constants can be transplanted into the lane path — the
scales and consumers are different and unproven.

---

## 5. What is NOT established

* **The actual constant values.** `X:$08F4/5/6` and `X:$03AF/$03B0` live in a
  low-X bank whose ROM→RAM initialisation segment is not located. The proven
  anchor for the `0x03xx` band (`X:$03B7 -> P:$0E489`, delta `0xE0D2`, from the
  five-fold axis signature) lands in `E70A` filler for the `0x08xx` band, so
  that bank is initialised from a different segment or is NVM-backed. A
  brute-force delta search left ~20 000 candidates — **uninformative, not
  negative.** The numbers must be read live, not guessed.
* **Relative magnitude.** That code 5 is limited *lower* than code 1 is the
  hypothesis, not a measurement. The code-1 arm is dynamic, so its value
  depends on runtime `X:$2DA0` and could in principle be smaller.
* **Whether this is the binding constraint.** `X:$2D53` was measured nonzero in
  203/448 phase-6 samples in joint8, so torque is being produced. Whether its
  magnitude is limited *here* rather than downstream is untested.

---

## 6. Proposed joint9 measurement

Read the limiters live before changing anything. The existing FD22 format-5
handler takes exactly 7 sources; a format-6 variant needs no new mechanism:

```text
X:$2DB9   per-state code          (which arm ran)
X:$2D4A   authority increment     (dispatcher 2 output)
X:$2D46   rate limit              (dispatcher 1 output)
X:$2DA0   runtime schedule input  (code-1 arm input)
X:$2DA5   scheduled clamp result
X:$2D4B   integrator state
X:$2D53   torque accumulator
```

The decisive comparison is **code 1 vs code 5 on the same drive**: log a
sustained LKA episode and a sustained LCA episode, then compare `X:$2D4A` and
`X:$2D46`. That directly measures the ratio of granted authority and needs no
firmware modification beyond telemetry.

If code 5's limits are indeed the smaller pair, the minimal change is to point
the two table entries for code 5 at the code-1 arms (`P:$2AFB0`, `P:$2B031`) —
a 2-word edit that reuses the OEM scheduled path rather than forcing a
constant, and leaves codes 0/2/3/4 untouched.

**Caveat on that patch.** It is a proposal, not a recommendation, and it must
not be flashed before joint9 measures the constants. The code-1 arm reads
`X:$2D9A`, a runtime cell whose value during an LCA episode is unknown — it may
be produced only by the LKA path, in which case redirecting code 5 there could
yield zero or an unbounded step rather than more authority. Measure first.

---

## 7. Cross-build comparison against BV6T (which has working LCA)

The two builds differ on **both** dispatchers — in *opposite directions*. This
is the strongest evidence in this document, and it corrects the framing of §1.

### Dispatcher 1 — the LCA-relevant difference

In both builds, codes 4 and 5 read one **shared source cell**:

```text
BV6T  code4 P:$273EF  8745 0100  F77C 0533  F67C 23A7 21ED  JSR divider
BV6T  code5 P:$273F9  F67C 23A7 21ED                        (no divider)
CV6T  code4 P:$2AFD8  8745 0400  F77C 03B0  F67C 2D50 2D46  JSR divider
CV6T  code5 P:$2AFE2  F67C 2D50 2D46                        (no divider)
```

The difference is **what that source cell contains at runtime**:

| Build | Source cell | Runtime writer | Verdict |
|---|---|---|---|
| BV6T-AF | `X:$23A7` | `P:$273BA` (computed) | **DYNAMIC** |
| CV6T-AR | `X:$2D50` | none — literal `#$0400` at `P:$2B513` | **CONSTANT** |
| CV6T-AH | `X:$2891` | none — literal `#$0100` at `P:$2963D` | **CONSTANT** |

BV6T recomputes the cell **every cycle**, by a producer called from the wrapper
immediately before the dispatcher:

```text
BV6T  P:$27460  E256 7346   JSR P:$27346   <- producer, writes X:$23A7
                E256 73BE   JSR P:$273BE   <- dispatcher 1
CV6T  P:$2B054  F27C 03B4   MOVE.W X:$03B4,C
                E256 AF90   JSR P:$2AF90   <- dispatcher 1, NO producer call
```

The BV6T producer `P:$27346..$273BD` is a two-segment interpolation over
**runtime** state plus calibration:

```text
runtime inputs:      X:$23DE (2 writers), X:$23DA (1), X:$21EB (1)
calibration inputs:  X:$0601, X:$0602 (read-only), table base $0A78
```

**Same-family control passes:** CV6T-AH behaves like CV6T-AR (constant), not
like BV6T. So this is a genuine family difference, not recompilation noise —
it satisfies the control this skill requires before any A-vs-B claim counts.

### Dispatcher 2 — reversed

| Build | code 1 / 4 arm | Verdict |
|---|---|---|
| BV6T-AF | `P:$2744A` → `MOVE.W X:$05FA,B` | **CONSTANT** |
| CV6T-AR | `P:$2B031` → `(X:$08F4 * X:$2DA0) >> 8`, clamped | **SCHEDULED** |

So CV6T *added* runtime scheduling on the live-LKA code while *removing* it
from the source shared by the live-LCA code. The net picture:

```text
              dispatcher 1 (rate)      dispatcher 2 (authority)
              code 4/5 source          code 1/4 arm
BV6T-AF       DYNAMIC (producer)       constant
CV6T-AR       constant 0x0400          SCHEDULED
CV6T-AH       constant 0x0100          SCHEDULED
```

### Correction to §1

§1 said both CV6T dispatchers "partition the codes the same way", implying a
single consistent LCA-suppression design. The cross-build view shows that is a
property of **CV6T only**, and the two dispatchers are not expressing one
intent — they are two independently retuned layers. The LCA-specific regression
is narrower than §1 implied: it is the loss of the *dynamic rate-limit source*
feeding codes 4/5, not a general flat-limiting policy.

### What this does NOT prove

- **Not proven that BV6T's dynamic value is larger.** The producer's output
  range is unmeasured; `PSCM_14C218_calibration_compare.md` already records
  that CV's `0x0400`/shift-10 and AH's `0x0100`/shift-8 normalise to the same
  unity scale, so "dynamic" ≠ "bigger". It could schedule *down* with speed.
- **Not proven CV6T lacks the producer code.** The claim is only that CV's
  wrapper does not call one and `X:$2D50` has no direct runtime writer. An
  indirect/pointer write remains theoretically possible (the standing OPEN
  item 4 of `dataflow_report.md`).
- **Not a licence to copy BV6T's value.** The two builds' scales, source cells
  and consumers differ; the RAM layouts are relocated.

### Consequence for joint9

Add the source cell to the telemetry — it is the single most diagnostic word:

```text
X:$2D50   dispatcher-1 code4/5 rate-limit source   (expect: constant 0x0400)
```

If it reads a fixed `0x0400` through an entire LCA episode while `X:$2D46`
tracks it, the mechanism is confirmed on the car. That is a read-only
observation requiring no behavioural change.

---

## 8. Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/verify_authority_limiter.py     # 23/23 checks
python3 work/disasm/dis_at.py CV6T 0x2AFB0 22           # code-1 rate arm
python3 work/disasm/dis_at.py CV6T 0x2B031 18           # code-1 authority arm
python3 work/disasm/dis_at.py CV6T 0x2B00B 12           # preamble + dispatcher 2
python3 work/disasm/dis_at.py CV6T 0x2B07C 40           # torque function
```

The verifier re-derives every structural claim above directly from the
binaries, including the negative control that park logic touches no lane cell.
It is written to be able to fail: it caught a wrong shared-arm claim in the
first draft of this document.
