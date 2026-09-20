# PSCM DID map — UCDS names bound to real identifiers

Source: `candump-2026-09-14_222146.log` (UCDS reading the PSCM) cross-referenced
with the UCDS parameter list. Mapping method: UCDS issues its 40 reads in list
order; **8 of 8 independently-known DIDs land on their correct names**, so the
positional binding is verified rather than assumed.

| # | DID | UCDS name | notes |
|---|---|---|---|
| 1 | `0202` | Number of Trouble Codes Set due to Diagnostic Test | |
| 2 | `3302` | Steering Wheel Angle | |
| 3 | `330C` | Steering Shaft Torque Sensor #2 | driver input |
| 4 | `D111` | ECU Power Supply Voltage | |
| 5 | `D117` | ECU Internal Temperature | |
| 6 | `D118` | Motor Current | |
| 7 | `D700` | Critical Software Parameter Monitoring #1 | |
| 8 | `DD00` | Global Real Time | free-running — see FD0A note |
| 9 | `DD01` | Total Distance | |
| 10 | `DD02` | Main ECU Voltage Supply | |
| 11 | `DD05` | Outside temperature | |
| 12 | `F108` | ECU Network Signal Calibration Number | **anchor** |
| 13 | `F110` | On-line Diagnostic Database Reference Number | **anchor** |
| 14 | `F111` | ECU Core Assembly Number | **anchor** |
| 15 | `F113` | ECU Delivery Assembly Number | **anchor** |
| 16 | `F124` | ECU Calibration Data #1 Number | **anchor** |
| 17 | `F15A` | NOS OSEK Network Management Version Number | |
| 18 | `F160` | NOS Diagnostic Version Number | |
| 19 | `F161` | NOS CAN Communication Layer Version Number | |
| 20 | `F180` | Boot Software Identification | **anchor** |
| 21 | `F188` | Vehicle Manufacturer ECU Software Number | **anchor** |
| 22 | `F18C` | ECU Serial Number | **anchor** |
| 23 | `F40C` | Engine RPM | |
| 24 | `F40D` | Vehicle Speed Sensor | |
| 25 | `D100` | Active Diagnostic Session | |
| 26 | `D10E` | HSCAN Network Management State | |
| 27 | `D701` | Critical Software Parameter Monitoring #2 | |
| 28 | `DD06` | Power Mode | |
| 29 | `DE00` | Vehicle Variant Tune Selector | **config candidate** |
| 30 | `E103` | Car Configuration Parameter Faults | |
| 31 | `F101` | Primary Bootloader Configuration | answered NRC |
| 32 | `F162` | Software Download Specification Version | |
| 33 | `F163` | Diagnostic Specification Version | |
| 34 | `FD03` | MicroHybrid enabled | |
| 35 | `FD0B` | Internal Fault Code | |
| 36 | `FD0C` | **Q-Axis Current** | torque-producing motor current |
| 37 | `FD0D` | D-Axis Current | field current |
| 38 | `FD0E` | **Torque Loop Demand** | **the EPS torque COMMAND** |
| 39 | `FD0F` | Motor Mechanical Velocity | |
| 40 | `FD20` | Mean Friction Share Histogram | |

## Why this matters

`PSCM_does_it_steer.md` records that every statistical proxy for "did the PSCM
apply torque" failed its own control, twice producing false positives that had
to be retracted. The root cause was stated there: **no EPS assist-output signal
exists on the CAN bus** — `TorsionBarTorque` on `0x140` is *driver input*
(hands off 0.108 Nm vs hands on 0.784 Nm, 7.3×).

`FD0E` **Torque Loop Demand** is that missing signal, and `FD0C` **Q-Axis
Current** is the current actually delivered to produce it. Both are readable
over UDS while the application runs.

## Hardware confirmation

Snapshots `dids_parked.json` (wheel at rest) and `dids_parked2.json` (torque
applied by hand), 39 DIDs each:

```
FD0C  Q-Axis Current      00 -> E4   (0 -> 228)
FD0E  Torque Loop Demand  00 -> EA   (0 -> 234)
```

36 of 39 unchanged. The two that moved are exactly the two the naming predicts.
This is an **independent confirmation of the positional mapping** — the names
were derived from read order, and the physical test agreed with them.

`FD0A` also differed but was eliminated as a free-running counter: across five
observations its first two bytes went 14257 → 14574 → 14155 → 14155 → 14383,
moving in *both* directions with no input change. Consistent with `DD00` Global
Real Time being a timer; `FD0A` was not in the UCDS read set.

## Caveats

- Scaling is unknown. `E4`/`EA` are raw bytes; whether they are Nm, percent or
  ADC counts is not established. Direction (sign) is also untested — the hand
  test applied torque in one direction only.
- `FD0C` read `F6` in the UCDS capture and `00` at rest in ours, so it does
  return to zero; it is a live reading, not a latched maximum.
- The mapping's validity rests on UCDS reading in list order. That is supported
  by 8/8 anchors plus the physical test, but a UCDS version that reorders its
  reads would invalidate it.

## The measurement this enables

Poll `FD0E` and `FD0C` while driving, alongside `0x0A5` lane state from
`la_monitor.py`. Then:

- **LKA episodes** (state 2/4) — expect non-zero Torque Loop Demand
- **LCA episodes** (state 6) — the open question
- **idle/suppressed** — the control; should be ~0

If `FD0E` is non-zero during LKA and zero during LCA, that settles the entire
investigation: the PSCM receives LCA commands and does not act on them. If it
is non-zero in both, LCA *is* applying torque and the complaint is authority,
not enablement — consistent with five traced firmware mechanisms all showing
LKA and LCA treated identically.


---

# Configuration DIDs (UCDS "Select Configuration")

A second UCDS screen exposes 15 writable configuration items. Their DIDs are
named directly in that UI, which confirms several `FDxx` identifiers
independently of the positional mapping above.

| DID | UCDS name | our read (parked) |
|---|---|---|
| `F190` | Vehicle Identification Number | `WF0AXXWPMAEL32600` |
| `FD01` | SAPP enabled | `00` |
| `FD04` | Rack Length Selector | `01` |
| `FD07` | PDC enabled | `01` |
| `FD08` | PDC initial torque | `0021` |
| `FD09` | CCP enable | `01` |
| `FD0A` | CPU Info | `374b3bdf01be` (varies) |
| `FD10` | Straigh Ahead Adaptation Angle | `0015` |
| `FD11` | ANC enable | `01` |
| `FD12` | Straight Ahead Adaptation enable | `00` |
| `FD13` | **Function enable** | `01000101` |
| `FD14` | Adaptation Algorithms value | `00210015` |
| `FD15` | **Lane Assist enable** | `01` |
| `FD21` | TSC enable | `01` |

`FD14 = 00210015` is exactly `FD08 (0021)` ++ `FD10 (0015)` — a composite view
of the two adaptation parameters.

## FD13 "Function enable" — five flags, and an unresolved contradiction

UCDS decodes `FD13 = 01000101` into five booleans:

```
PDC enable                        TRUE
Straight Ahead Adaptation enable  TRUE
ANC enable                        FALSE
LA enable                         TRUE
TSC enable                        TRUE
```

Our own read of `FD13` is byte-identical to the UCDS dialog (`01000101`), so
both tools see the same ECU state.

**But the flags do not agree with the standalone DIDs for the same features:**

| feature | FD13 says | standalone DID | agree? |
|---|---|---|---|
| PDC enable | TRUE | `FD07` = `01` → TRUE | yes |
| Straight Ahead Adaptation | TRUE | `FD12` = `00` → FALSE | **NO** |
| ANC enable | FALSE | `FD11` = `01` → TRUE | **NO** |
| LA enable | TRUE | `FD15` = `01` → TRUE | yes |
| TSC enable | TRUE | `FD21` = `01` → TRUE | yes |

Three agree, two disagree — and in *opposite* directions, so it is not a simple
inversion. Note also that `FD13` is 4 bytes while 5 booleans are displayed, so
the layout cannot be one byte per flag in display order.

The bit layout is **not determined** by the screenshot: the `0..7 / 8..15 /
16..23 / 24..31` row is the dialog's generic bit ruler and the `0`–`4` markers
are UI row indices, not bit numbers.

> **Do not write `FD13` until this is resolved.** A plausible explanation is
> that `FD13` and the standalone DIDs are different views (commanded vs
> effective, or a configuration not yet applied), but that is untested.

## What this says about the LKA/LCA question

**Lane assist is enabled in configuration** — `FD15 = 01` and `FD13`'s LA flag
both read TRUE, and these two agree.

There is **no separate LCA / lane-centering flag** anywhere in the 15-item
configuration list. The only FALSE in the entire set is ANC (Active Nibble
Control), a steering-shimmy damper unrelated to lane keeping.

So the configuration route closes the same way the firmware route did: nothing
here distinguishes LKA from LCA. That is consistent with all five traced
firmware mechanisms (`X:$0904`, `X:$2DC1`, the per-state codes, the torque
function, and the code-4/code-5 transition), every one of which treated the two
modes identically.
