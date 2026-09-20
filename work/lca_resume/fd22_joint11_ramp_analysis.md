# Joint11 — ramp fix: LCA now steers

Drive of 2026-09-20, `fd22_joint11.csv`, 3910 samples over 391.4 s, zero
timeouts, zero decode errors. Firmware `CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF`
(sha256 `ab39234a…82a6`).

Driver report: **"it is definitely steering."**

## Result

```text
                        joint9    joint10   joint11
peak |accumulator| LCA      28        54      2000
peak |accumulator| LKA    1923      2732      1676
LCA / LKA                0.015     0.020      1.19
```

**37x improvement over joint10, and LCA authority now exceeds LKA's.**

| | joint10 | joint11 |
|---|---:|---:|
| code-5 samples | 907 (38.2 %) | 1145 (29.3 %) |
| sustained code-5 episodes | 30 | 25 |
| longest code-5 episode | 7.21 s | **9.41 s** |

## The fix worked exactly as modelled

**Ramp unblocked.** `X:$2D47` was pinned at 0 in code 5; it now ranges
80…1024 and sits **at its clamp in 95.6 % of samples**. The limiter that
joint10 measured as "completely slack" is now the binding constraint again —
which is the correct end state.

**The demand model is confirmed exactly:**

```text
demand == (control_law * ramp) >> 10
1144/1144 samples agree within ±1  (100.0 %)
```

This validates the reading of `P:$2B054` and, retrospectively, the whole
route-8 chain. The columns mean what they were claimed to mean.

## Two defects, same shape

| Round | Patch | Defect |
|---|---|---|
| joint10 | `P:$2B02F B04E→B031` | authority increment `X:$2D4A` was **−9** (decay) not **+40** |
| joint11 | `P:$2AFAE AFE2→AFD8` | ramp increment was a **literal 0** (`E580`) not a computed value |

In both, sustained-LCA (code 5) was routed to a degenerate arm while LKA
(code 1) and LCA-entry (code 4) got the working one. Two words total.

---

# The drop-out question: PSCM or IPMA?

**Asked:** when LKA/LCA stops steering mid-turn, is that the PSCM giving up or
the IPMA withdrawing the request?

> **ANSWERED from pre-patch raw CAN — it is the IPMA**, on a fixed ~3.7 s
> intervention timer, with the PSCM still granting throughout. The analysis
> below (which excludes a PSCM torque limit) was right as far as it went; the
> discriminator did not need new telemetry, because five pre-patch `candump`
> logs already contain all three layers. The ~3.7 s cap and its evidence are
> summarised in `PSCM_LCA_investigation.md`. The joint12 proposal at the end
> of this section is therefore **not needed** and was not built.

**Status: NOT YET ANSWERED — but one whole family of causes is now excluded.**

## What the data rules OUT

**It is not a PSCM torque/saturation limit.** Nothing internal is clipping at
the moment of the drop:

```text
peak |accumulator| during code 5 : 2520
hard saturation (P:$2B09B)       : 32766     (77x headroom)
peak |control_law|               : 2748
```

**Demand is healthy — often rising — when the episode ends.** Across 25
code-5 episodes of ≥10 samples:

```text
|control_law| at the LAST sample : median 756
|control_law| mid-episode        : median 506
episodes ending ABOVE mid-episode median : 15/25
```

The longest episode (9.41 s) ended at its own peak, `control_law = −2264`,
and handed straight over to LKA. That is the opposite of a module running out
of authority: the command was at full strength and simply stopped being
acted on.

So the drop is **not** the PSCM hitting a ceiling. That matches the
"mid-turn" symptom: these ends cluster at elevated demand, i.e. in the
curve, not on the straight.

## What the data cannot yet distinguish

`X:$2DB9` (the per-state code) is the **PSCM's own state-machine output**.
Seeing it leave 5 does not say *who* caused the exit. Three layers are
involved and only the third is instrumented:

```text
1. raw CAN 0x0A5 request    <- IPMA's intent            NOT LOGGED
2. X:$2DDE  lane state      <- what the PSCM dispatcher accepted   NOT LOGGED
3. X:$2DB9  per-state code  <- what the consumer did    logged
```

The middle layer matters: §2.1 proved the dispatcher can veto a request
(the `X:$0904` gate blocked LCA entry entirely until it was NOPed). So
`X:$2DDE` dropping is *not* proof the IPMA withdrew — the PSCM may have
refused an request that was still being sent.

## Exit census (this drive)

Of 26 code-5 exits:

```text
5 -> 4 : 10    re-entry; the state machine re-arms, LCA continues
5 -> 0 :  7    to idle
5 -> 3 :  6    to idle
5 -> 1 :  3    handover to LKA
```

The `5→4→5` cycle is likely normal re-arming rather than a true stop, so the
**true** drop count is ~13, not 26. Any analysis of "why does it stop" must
exclude the re-entry cycle or it will explain the wrong event.

## The discriminator (joint12)

Log all three layers at once and compare their edge ORDER:

| Observation | Conclusion |
|---|---|
| `0x0A5` request leaves 6 **first**, then `X:$2DDE`, then `X:$2DB9` | **IPMA withdrew** — camera lost the lane / hit its own limit |
| `0x0A5` still 6 while `X:$2DDE` drops | **PSCM dispatcher vetoed** — a gate upstream of the consumer |
| `0x0A5` and `X:$2DDE` both hold while `X:$2DB9` leaves 5 | **PSCM consumer aborted** — a condition inside the state machine |

Layers 2 and 3 are internal, so a format-8 handler covers them:

```text
X:$2DDE  lane state (dispatcher output)
X:$2DB9  per-state code
X:$2DC1  consumer gate      (known to gate phase-1 entry)
X:$2DAF  state-machine phase
X:$220F  the retained B==3 condition
X:$2DBA  auxiliary availability predicate
X:$2D54  control_law        (keeps the demand visible at the edge)
```

Layer 1 needs raw CAN in parallel — `candump -L can0` — and the existing
joint tooling to align it, exactly as joint2…joint8 did. Telemetry-only, so
the joint10+joint11 fixes stay in place and the drive behaves as this one did.

## Caveats

- **Authority now slightly exceeds LKA** (ratio 1.19). Not necessarily wrong —
  LCA is a sustained centring task and LKA a brief nudge — but if it ever
  feels over-eager, the knob is `X:$03B0` (divider numerator, shared with
  code 4 only, **not** with LKA).
- LKA's own peak fell from 2732 to 1676 between drives. Probably different
  road/inputs rather than an effect of the patch, but it is unexplained and
  means cross-drive LKA comparisons should be treated loosely.
- Only 3 sustained LKA episodes this drive; the LKA column is thin.
- The `5→4` re-entry cycle is assumed normal and has not been verified.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_joint11_demand.py fd22_joint11.csv
```
