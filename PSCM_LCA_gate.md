# The LCA gate in the PSCM — found, verified, flashed, REFUTED

> **CLOSED 2026-09-14: the gate is NOT the cause. Patching it changed nothing.**
>
> The 4-word calibration guard on the `== 6` arm is real and was correctly
> identified — read back from the module's own flash after flashing:
>
> ```
> 0x054EFC: 4C06 A207 E700 E700 E700 E700 E684 2DDE A902 E680
>           == PATCHED: True   == STOCK: False
> ```
>
> With the guard NOPed, `LkaActvStats_D_Req == 6` enters the LCA torque state
> unconditionally. **The vehicle behaved exactly as before.** Driver report:
> *"It feels like nothing changes, it is wanders from lane to lane until LKA
> kicks."*
>
> Measured, not merely perceived — if LCA were centring the car, the
> drift-to-line/LKA-correction cycle would become rarer. It did not:
>
> | log | firmware | LKA episodes / 10 min |
> |---|---|---|
> | drive1 | stock | 8.1 |
> | drive3 | stock | 11.6 |
> | **drivemod** | **PATCHED** | **22.5** |
>
> LCA *display* activity rose sharply (23 episodes / 49.2 s engaged vs 3 / 22.4 s
> and 10 / 10.9 s) because the IPMA thresholds were lowered — but the LKA rate
> did not fall, so no centring torque is being applied.
>
> **Conclusion: `X:$0904` was already `1` at runtime; the gate was open all
> along.** Removing an always-passing check is a no-op, which is exactly what was
> observed. The original (retracted) RAM reading of `1` appears to have been
> correct by luck, not by method — see the retraction below, which still stands:
> that dump could not have shown runtime state.
>
> **Module health after the flash, verified on the bus:**
> `22 F188 = CV6T-14C217-AR`, `22 F124 = CV6T-14C218-AX`, 19 DTCs stored but
> **0 confirmed** — every one is status `0x40 testNotCompletedThisCycle`. No
> software-integrity fault, so the word-A/word-B checksum repair was correct.
>
> **Recommendation: revert to stock `CV6T-14C217-AR.VBF`.** The patch provides
> no benefit and leaves a modified steering controller in the car for nothing.

## The RAM-read retraction (still valid, and now independently confirmed)

Reading `X:$0904` through the SBL after the flash returned **`0000`**, where the
2026-09-13 dump returned **`0001`** — same address, different value, because
loading the SBL destroys application RAM. Neither value is what the running
application sees. This is direct proof that SBL-mediated RAM reads cannot
answer runtime questions, exactly as the user suspected when he asked.

## What was found


The CV6T PSCM **does** recognise `LkaActvStats_D_Req == 6` (LCA in Progress) and
**does** contain the code path that enters the LCA torque state. That path is
guarded by a single conditional that BV6T does not have.

Reproduced independently from the raw binaries by `work/disasm/verify_lca_gate.py`
(a separate implementation from the one that found it — all checks PASS):

```
BV6T-14C217-AF  @P:$2691D          CV6T-14C217-AR  @P:$2A77E
  4C06        CMP.W  #6              4C06        CMP.W  #6
  A203        Bne   +3               A207        Bne   +7
                                     F07C 0904   MOVE.W X:$0904,A   <-- inserted
                                     4C01        CMP.W  #1          <-- inserted
                                     A203        Bne   +3           <-- inserted
  E684        MOVE.W #4,...          E684        MOVE.W #4,...
  2DDE        (store)                2DDE        (store)
  A902        BRA                    A902        BRA
  E680        MOVE.W #0,...          E680        MOVE.W #0,...
  2DDE        (store)                2DDE        (store)
  E708        (fallthrough)          E708        (fallthrough)
```

Four words inserted into the `== 6` arm **only**. If `X:$0904 != 1`, control
falls through to the same `store 0` (idle) path as an unrecognised enum value —
the LCA torque state is never entered.

The `== 2` and `== 4` arms (LKA left / right) are **byte-for-byte identical
across all three builds** and carry no such guard:

```
CV6T-AR: 4C02 A203 E681 2DDE   4C04 A203 E682 2DDE
BV6T-AF: 4C02 A203 E681 23E4   4C04 A203 E682 23E4   (only the store target differs)
```

**This is exactly the asymmetry the symptom predicts**: LCA is recognised (the
dash shows it, because the display path is upstream of this gate) but the torque
state is never reached, while LKA is unaffected.

## What `X:$0904` is

In CV6T-AR the cell is **read exactly once, in this gate, and never written**
anywhere in the image. It sits inside a contiguous run of similar cells:

```
X:$0900 $0901 $0902 $0903 $0904 $0905 $0906 $0907 $0908 $0909 $090C $090D $090F
```

every one of which is accessed by a load (`F07C` / `F17C` / `F57C`) and none of
which is written. That is the signature of a **configuration block** — values
placed in RAM at boot from EEPROM or data flash and thereafter treated as
read-only parameters.

If that reading is right, LCA on this module is **disabled by configuration data,
not by missing code** — which would make it changeable without patching program
flash.

## What is NOT established

1. **The runtime value of `X:$0904` is unknown.** The entire conclusion — that
   the gate is what blocks LCA on this car — rests on it being ≠ 1, which has
   not been observed. It has only been shown that a gate exists which *would*
   block LCA if the cell is not 1.
2. **Where the cell gets its value is unknown.** No initialising write was found.
   It may be written through pointer/indexed addressing that a literal-operand
   scan cannot see, or copied by a block-init routine, or shadowed from EEPROM.
3. **This is a CV6T-family trait, not an AR regression.** The control build
   CV6T-14C217-AH has the same gate at `P:$28CF7`. Per the brief's own rule, a
   difference present in both AH and AR is not evidence of a deliberate
   AR-specific disable.
4. **`X:$0904` in BV6T is a different variable.** BV6T references that address
   three times, in unrelated code — addresses are not comparable across builds.
   The BV6T `== 6` arm simply has no gate; it does not "set the cell to 1".

## Corroborating results from the other two investigations

**Structural comparison — NEGATIVE, and usefully so.** BV6T contains no
lane-assist subsystem that CV6T lacks. CV6T-AR is the *larger* build (102 845 vs
89 575 traced instructions, 1868 vs 1685 functions). The literal `CMP #6` test
appears 72× in BV6T and 73× in CV6T — no deficit. Feature removal predicts the
opposite asymmetry, so "CV6T had LCA stripped out" is refuted. A small gate is
exactly what remains possible, and that is what was found.

**Configuration route — narrow but not closed.** This vehicle family's PSCM has
**no As-Built configuration blocks at all**: across 24 As-Built exports, 23 have
a `730` identification record with part numbers only and zero `730-xx-xx` data
blocks. Every forum recipe quoting "PSCM 730-01-01 = lane assist enable" is from
North American trucks and other platforms whose PSCMs do carry those blocks;
**those addresses do not exist on this module**. The only configuration surface
is a UCDS "Direct Configuration" set of ~13 `FDxx` DIDs plus an undocumented
`DE00`, of which 8–10 have genuinely unknown meaning and do vary between
vehicles. A lane-capability enum among them cannot be excluded, but no source
assigns a lane meaning to any of them.

That finding and the firmware finding fit together: if `X:$0904` is loaded from
EEPROM/configuration, one of those unknown `FDxx` bytes is a plausible origin.

## The decisive next step — read the cell, do not patch anything

`X:$0904` is a **word address**; the UDS byte address is
`0x04000000 + 2 × 0x0904 = 0x04001208`.

`ReadMemoryByAddress` (0x23) is **absent** on this module (documented in
`PSCM_flash_reading.md`: all three address/length forms return NRC 0x11), but
`dump_xram.py` already reads X: RAM through the SBL + RequestUpload route, and
the earlier probe showed `0x04002000` (X:$1000) readable. `X:$0904` is *below*
that boundary, so readability must be established rather than assumed.

**Read it while the application has been running** — the value is only meaningful
after boot-time initialisation, and the SBL must be loaded without power-cycling
in a way that clears RAM.

Outcomes:

* **Cell reads 1** → the gate is open and LCA is blocked by something else
  entirely. The whole line of investigation is wrong and must restart.
* **Cell reads ≠ 1** → the gate is confirmed as the blocker. Then the question
  becomes *where the value comes from*, and the right target is that source
  (EEPROM byte / configuration DID), **not** program flash.

## The consumer path is intact too — a further negative

After the gate was ruled out, the obvious follow-up was whether anything
downstream discriminates against the LCA state. It does not.

The dispatcher writes the lane state to `X:$2DDE` (CV6T-AR) / `X:$23E4`
(BV6T-AF). Every read of that cell and the constant it is compared against:

```
BV6T-14C217-AF  X:$23E4   {#1: 7, #2: 1, #4: 4, #129: 2}   tests for LCA state: 4
CV6T-14C217-AR  X:$2DDE   {#1: 7, #2: 2, #4: 4, #129: 4}   tests for LCA state: 4
```

**Both builds test for state 4 at exactly four downstream sites.** The LCA
consumer path is present in CV6T in the same measure as in BV6T. There is no
deficit to find here either.

Combined with the structural comparison (`CMP #6`: 72× BV6T, 73× CV6T), this
shows the LCA *consumer* path is fully present in CV6T. It says nothing about
whether the gate lets control reach it — that depends on `X:$0904`, whose
runtime value is **unknown** (see the retraction at the top).

## Where that leaves the question

The PSCM was the prime suspect because the IPMA demonstrably sends valid LCA
commands and the dash demonstrably shows LCA. That suspicion is now
substantially weakened. What has *not* been excluded:

* The PSCM may act on LCA correctly, with the torque simply too small to feel —
  **this has never been measured**, because `TorsionBarTorque` is driver input,
  not assist output, and both drive logs are confounded (see
  `PSCM_does_it_steer.md`). The same measurement gap applies to LKA.
* The gate cell `X:$0904` was read in one ignition cycle. No write to it exists
  anywhere in the image, so it should be static — but it is one sample.
* A precondition evaluated elsewhere (hands-on, driver-torque override, a
  timeout) could suppress torque without touching the lane-state machinery.

The next informative step is unchanged and is **not** a firmware change: run the
`PSCM_does_it_steer.md` protocol to establish whether the PSCM applies torque
for *any* lane state on this vehicle. Until that is known, "LCA does not work"
cannot be distinguished from "lane assist torque is not observable from CAN".

## Why the gate must not be patched (retained, now moot)

Patching it out is technically trivial — change `A207` back to `A203` and NOP
the four inserted words. **It would also have achieved nothing**, since the gate
already passes. Had it been done on the strength of the static finding alone,
the result would have been a flashed steering controller, an unchanged symptom,
and a false belief that the cause had been addressed.

The original reasons for not patching are retained because they still apply to
any future candidate:

1. **The premise is unverified.** We have not shown the cell is ≠ 1, nor that
   the PSCM applies torque for *LKA* on this vehicle (see
   `PSCM_does_it_steer.md` — both drive logs are confounded and cannot answer
   it). Patching a steering controller to fix a fault that has not been
   demonstrated is the wrong order of operations.
2. **The gate may be load-bearing.** A configuration cell consulted at the entry
   to a torque state may be gating on hardware variant, sensor set, or a
   calibration that is genuinely absent on this car. Forcing the state on does
   not supply whatever the gate is checking for.
3. **A configuration origin, if confirmed, is reversible and safe.** A flash
   patch is neither.

The cheap, reversible, informative step is to read one 16-bit cell. Everything
else waits on it.
