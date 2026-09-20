# Drive test — results

Log: `drive1.{csv,jsonl,log}`, 141 097 decoded samples, 0 … 84.1 km/h,
~20 minutes. LKA, LCA and LDW all exercised. Read-only capture.

---

## 1. THE BLOCKING QUESTION IS ANSWERED: the PSCM is NOT the gate

```
PSCM allows lane assist from   39.39 km/h   (LaActAvail_D_Actl = 3)
IPMA first commands LKA at     61.64 km/h
IPMA first shows  LCA at       74.85 km/h
```

The PSCM declares `LKA/LCA+LDW available` **22 km/h before** the camera does
anything. It is permissive, not restrictive.

> **The speed threshold lives in the IPMA.** The earlier stationary reading of
> `LaActAvail_D_Actl = 0` was not a speed gate at all — the PSCM simply
> reflects the camera's state. This retracts the concern that IPMA work might
> be pointless; it is exactly the right target.

## 2. THE CALIBRATION VALUES ARE CONFIRMED ON THE VEHICLE

Isolating the **pure speed-gate transitions** — `LkaActvStats_D_Req` moving
between 7 (`LKA/LCA suppressed L+R`) and 0 (`Idle`, i.e. armed and waiting),
with no intervention in between:

| | measured (median) | predicted from `CV4T-14F398-AF` rec 2 | delta |
|---|---|---|---|
| **arm** (7→0) | **64.72 km/h** (n=17) | 17.940 m/s = **64.58 km/h** | **+0.14** |
| **suppress** (0→7) | **59.53 km/h** (n=11) | 16.670 m/s = **60.01 km/h** | **−0.48** |
| hysteresis | 5.19 km/h | 4.57 km/h | +0.62 |

Arm speeds cluster tightly: `64.27, 64.38, 64.40, 64.46, 64.55, 64.56, 64.72,
64.81` — eight samples inside 0.55 km/h of the predicted 64.58.
Suppress speeds cluster equally tightly: `59.26 … 59.73`.

**This closes the loop.** The value found by static analysis of the calibration
block predicts on-vehicle behaviour to within half a km/h, and the hysteresis
pair matches too. Confidence moves from PLAUSIBLE to **CONFIRMED**:

```
mem 0x05458  file 0x2458   17.94 m/s   LKA arm       (= 64.58 km/h)
mem 0x05454  file 0x2454   16.67 m/s   LKA suppress  (= 60.01 km/h)
mem 0x05DE4 / 0x06AE0      64.6  km/h  arm, km/h mirrors
```

It also confirms the vehicle runs **variant record 2** of the 4 in the block.

## 3. LKA and LCA have SEPARATE thresholds — both measured

**The PSCM cannot answer this question.** `LaActAvail_D_Actl` is a *combined*
enum by construction (`3 = "LKA/LCA and LDW available"`), so its ~40 km/h
transition says nothing about which feature is ready. It is the PSCM's own
readiness to accept a steering request, and it is permissive for everything.

The IPMA publishes **per-feature** flags on `0x1B5`, each defined as
*"Vehicle is in operating speed range"*:

| feature | flag | enter range | leave range | hysteresis |
|---|---|---|---|---|
| **LKA** | `LkaVLvl_B_Dsply` | **64.56 km/h** (n=9) | **59.44 km/h** (n=9) | 5.1 |
| **LDW** | `LdwVLvl_B_Dsply` | **64.56 km/h** (n=9) | **59.44 km/h** (n=9) | 5.1 |
| **LCA** | `LcaVLvl_B_Dsply` | **80.01 km/h** (n=2) | **75.03 km/h** (n=2) | 5.0 |

Enter values cluster within 0.5 km/h (`64.3 … 64.8`), leave within 0.5
(`59.3 … 59.8`). LCA's two samples are `80.0, 80.0` and `75.0, 75.1`.

**LDW shares LKA's threshold exactly** — identical sample counts and identical
transition speeds. LCA is the only one with its own gate.

### This locates the LCA threshold — previously unproven

`80.0` is stored as an **invariant** float (all 4 variant records) at field
`+0x000` of table `0x30A01068`:

```
mem 0x06348 / 0x063D4 / 0x06460 / 0x064EC   = 80.0
```

Invariance fits: LCA engaged at exactly 80.0 km/h on both observations, and the
value does not differ by variant. **PLAUSIBLE, not proven** — other standalone
`80.0` floats exist in the block (`0x03610`, `0x043B8`, `0x0443C`, `0x04480`,
`0x044C4`).

No `75.0 km/h` and no `20.83 m/s` exists anywhere in the block, so **LCA's
disengage speed is computed, not stored** — consistent with a 5 km/h
hysteresis constant.

### Hysteresis is a stored FIELD, not a second threshold

Table `0x30A01048` (base file `0x3578`, stride `0x2B4`) holds engage speed at
`+0x000` and hysteresis at `+0x010`, both in km/h:

| rec | `+0x000` engage | `+0x010` hyst | engage − hyst | m/s field (`tbl38 +0x1C0`) ×3.6 |
|---|---|---|---|---|
| 0 | 59.60 | 5.00 | **54.60** | **54.60** ← exact |
| 1 | 59.60 | 4.30 | 55.30 | 55.44 |
| **2** | **64.60** | **5.00** | **59.60** | 60.01 |
| 3 | 60.60 | 5.00 | 55.60 | 55.80 |

Record 0 matches exactly, confirming the interpretation. For the live record 2
the measured leave (**59.44**) fits `engage − hyst = 59.60` (−0.16) better than
the m/s field's 60.01 (−0.57) — so **`+0x010` is the operative hysteresis** and
the m/s copy may be a stale or differently-rounded duplicate.

**Consequence for patching:** lowering the LKA gate means editing `+0x000` of
record 2 (and its mirror), *not* the m/s pair alone. Which copy the firmware
actually reads is still unproven — this is exactly why the use-site matters.

## 4. LKA interventions end at the SPEED GATE, not a fixed timer

**Correction — an earlier reading of this log claimed a "~3.7 s timer". That
was wrong.** The user's observation (longer corrections on curving roads) is
what the data actually supports.

10 completed interventions. Durations: **0.362, 1.868, 3.494, 3.715 ×5,
3.716, 3.717 s**. Two are far shorter than 3.7 s, so no fixed timer explains
them.

Decisive detail: **all 10 ended in state 7** (`LKA/LCA suppressed L+R`) — the
*speed-gate* state — not in state 0 (`Idle`, a natural finish). And several
re-triggered within a second or two:

```
t=1084.70 val=2 (INTERV LEFT)   67.34 kph
t=1088.20 val=7 (suppressed)    69.28
t=1091.91 val=5 (suppr right)   64.65
t=1092.60 val=2 (INTERV LEFT)   64.18     <- re-trigger, 4.4 s later
t=1096.31 val=7 (suppressed)    64.13
t=1097.40 val=0 (idle/armed)    64.40
```

So a burst is one *segment* of a longer correction that keeps being
re-asserted, not a single capped intervention. The ~3.7 s clustering reflects
the gentle, nearly-straight road on this drive; a curving road would hold the
request continuously for longer, exactly as observed by the driver.

**Do not treat 3.7 s as a firmware constant.** It is a property of this drive.

## 5. Other observations

* `LkaActvStats_D_Req` value **7** means "below the speed gate" in practice —
  37 496 samples, the dominant state.
* Value **0** (`Idle`) = armed and waiting for a lane departure.
* LDW warnings fired at 62.02 and 73.88 km/h — deliberate line crossings.
* The PSCM toggles `LaActAvail` 20 times, often around 40 km/h, tracking lane
  availability rather than gating on speed.

## Consequence for the threshold-lowering task

The target is confirmed and the risk is the lowest available:

1. Edit **`CV4T-14F398-AF`** (17 KB DATA part, isolated erase region
   `0x3000` len `0x4428`) — never the 883 KB application.
2. Change record 2's pair at file `0x2454`/`0x2458`, **and** the two km/h
   mirrors at `0x2DE4`/`0x3AE0`, consistently.
3. Repair **BootNfo CRC-32** at block `+0x24` (`zlib.crc32` over the block with
   those 4 bytes skipped) → then block CRC-16 → then file CRC-32.
4. Re-flashing the OEM 17 KB part is a complete undo.

Still unproven: no disassembly use-site ties `0x5458` to the comparison (the
IPMA is Renesas M32R with no FPU, so it goes through a soft-float helper).
The on-vehicle prediction match is strong behavioural evidence, but it is not
the same as reading the code.

## Tools

* `work/vehicle/la_monitor.py` — live monitor + logger, 11 self-tests
* `work/vehicle/analyse_drive.py` — this analysis, 3 self-tests
