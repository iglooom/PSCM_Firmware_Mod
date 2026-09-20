# Joint10 — LCA authority fix: partial success, bottleneck moved

Drive of 2026-09-20, `fd22_joint10.csv`, 2377 samples over 237.8 s, zero
timeouts, zero decode errors. Firmware `CV6T-14C217-AR_LCA_JOINT10_FIX.VBF`
(sha256 `c41065a3…4784`), one-word dispatcher-2 redirect.

## The patch did exactly what it was designed to do

```text
                        joint9 (before)   joint10 (after)
authority_increment          -9               +40
```

Sustained LCA now runs the build arm. Confirmed live, constant across all 907
code-5 samples. **The flash landed and the mechanism works as intended.**

## LCA engagement improved dramatically

| | joint9 | joint10 |
|---|---:|---:|
| code-5 samples | 84 (4.0 %) | **907 (38.2 %)** |
| sustained code-5 episodes | 2 | **30** |
| longest code-5 episode | 0.7 s | **7.21 s** |
| code-4 entry episodes | 35 (all ≤0.1 s) | 18 |

Before, LCA state collapsed almost immediately and the integrator was dragged
to zero every time. Now it sustains for seconds and holds a stable non-zero
value. The decay term is gone — this is a real, measured improvement in the
state machine's behaviour.

## But the torque is still ~50x too small

```text
peak |accumulator|     joint9      joint10
  LKA code 1            1923        2732
  LCA code 5              28          54
```

LCA roughly doubled, and remains two orders of magnitude below LKA.

## Why: the limiter is no longer the constraint

> **CORRECTED 2026-09-20 — the conclusion below is right, the diagnosis of
> *which* dispatcher-1 word matters was wrong.**
> This section calls dispatcher 1 "numerically neutral" because the clamp
> `X:$2D46` measured 1024 in both modes. True of the clamp — but the rate
> limiter takes a **second** input, the per-cycle *increment* (Y0), and that
> is where the arms differ:
> `code 1/4 -> divider result`, **`code 5 -> literal 0` (`E580`)**.
> With a zero increment the ramp `X:$2D47` can never grow, so the demand
> `(X:$2D54 * X:$2D47) >> 10` stays near zero — which is exactly *why* the
> steps never approached the bound. The limiter was slack because there was
> nothing to limit. Fixed in joint11 (`P:$2AFAE AFE2 -> AFD8`); see
> `fd22_joint11_ramp_test.md`.

Per-cycle integrator step, measured on episode bodies:

| | steps | `|step| ≥ 40` (limit binding) | mean `|step|` | max |
|---|---:|---:|---:|---:|
| LKA code 1 | 174 | **140 (80.5 %)** | 131.6 | 876 |
| LCA code 5 | 727 | **0 (0.0 %)** | **1.9** | 17 |

Under LKA the +40 allowance is hit constantly — the limiter genuinely governs.
Under LCA the steps never come close to it: the largest is 17, the mean is 1.9.

**The limiter is now completely slack in LCA.** Raising it further would change
nothing. The constraint has moved upstream to the demand itself.

> **Consequence for the earlier plan.** §8 of `fd22_joint10_fix_test.md`
> proposed `X:$08F4` (+40) as the tuning knob if the result were too weak.
> That is now **refuted**: the drive proves +40 is never reached in LCA, so
> increasing it is provably a no-op. Do not build that patch.

## Where the demand comes from

`P:$2B054` computes the demand `X:$2D49` as a product:

```text
P:$2B05A  MOVE.W X:$2D54,A      ; aggregate / control-law output
P:$2B05D  MOVE.W X:$2D47,A      ; ramp (rate-limiter output, bounded by X:$2D46)
P:$2B061..P:$2B065               ; multiply, >> 10
          D57C 2D49              ; X:$2D49 = (X:$2D54 * X:$2D47) >> 10
```

`X:$2D49` is then the demand the torque function `P:$2B07C` chases, with
`X:$2D4B` following it under the per-cycle bound we just relaxed.

So a small LCA torque now implies a small **`X:$2D54`** (the control-law
output) or a small **`X:$2D47`** (the ramp), or both. Neither is instrumented
yet. `X:$2D46` measured 1024 in both modes, so the ramp's *bound* is equal —
but the ramp's *value* is not the same thing and has never been observed.

## Status of the overall question

- **Confirmed and fixed:** the code-5 decay term. Genuine progress; LCA now
  sustains instead of collapsing.
- **Newly proven:** the per-state limiter is not what caps LCA torque. Route 8
  identified a real defect, but not the dominant one.
- **Open:** why the LCA demand is ~50x smaller than LKA's. This is upstream of
  everything measured so far.

## Caveats

- **The subjective result is unknown.** Whether the driver felt any change is
  the acceptance criterion and has not been reported. A 2x increase from a
  negligible baseline is probably still imperceptible.
- Code 3 remains 39.3 % of samples with unverified meaning.
- `torque_accumulator` frequently reads as the negated integrator in code 5
  (`P:$2B089 NEG B` on a sign test). Sign handling in this function is not
  fully traced; magnitudes are the safe reading, not signs.
- LKA `max |step|` of 876 exceeds the +40 bound, so `X:$2D4B` has a path that
  is not step-bounded — the limiter is not a simple slew clamp in all cases.
  Not yet explained; does not affect the LCA conclusion, where no step
  approaches the bound.

## Next measurement (joint11)

Instrument the demand chain. The format-6 handler takes 7 sources; swap the
now-answered limiter words for the upstream ones:

```text
X:$2DB9   per-state code        (keep: identifies the mode)
X:$2D49   demand                (the value the torque fn chases)
X:$2D54   aggregate / control-law output   <- prime suspect
X:$2D47   ramp value            (not its bound)
X:$2D4B   integrator            (keep: shows the follower)
X:$2D53   torque accumulator    (keep: the output)
X:$2D9A   code-1 arm runtime input (context for the LKA side)
```

Telemetry-only again, so the control path stays exactly as joint10 — which
means the joint10 fix should be **retained**, not reverted: it is a genuine
improvement and the drive showed no ill effects in the data.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint10.csv
```

Note: the analyzer prints `HYPOTHESIS REFUTED` on this file. That is the
**expected and desired** output for joint10 — it tests "is LCA limited below
LKA on these words", and after the fix it is not. The tool was written for
joint9's question.
