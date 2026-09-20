# Joint9 — limiter chain measured live

Drive of 2026-09-20, `fd22_joint9.csv`, 2106 decoded samples over 210.7 s,
zero timeouts, zero decode errors. Firmware
`CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF` (sha256 `30d9f4f6…4c6c`), telemetry
only — control path byte-identical to joint8.

## Headline

**The mechanism is found, and it is stronger than the hypothesis.** The
per-state authority increment `X:$2D4A` does not merely get a *smaller* value
under sustained LCA — it gets a **negative** one:

```text
                       LKA code 1      LCA code 5
authority_increment         +40             -9
```

`X:$2D4A` is added to the integrator `X:$2D4B` each cycle at `P:$2B08C`, and
`X:$2D53` (the torque accumulator) is derived from that integrator. A positive
increment lets torque build; a negative increment **bleeds it toward zero**.

Sustained LCA therefore cannot hold torque by construction. This is a decay
term, not a cap.

## The calibration constants, finally resolved

Static analysis could not resolve these (the low-X init segment is unlocated;
brute force left ~20 000 candidates). The drive reads them directly, and every
code is perfectly deterministic across all 2106 samples — one distinct value
per cell per code, no scatter:

```text
X:$08F4 =  40  (0x0028)   code 1/4 arm, scaled by X:$2DA0=256 (unity) -> +40
X:$08F5 = -33  (0xFFDF)   code 0/3 arm  (idle bleed)
X:$08F6 =  -9  (0xFFF7)   code 2/5 arm  (sustained-LCA bleed)
```

The measured mapping confirms the dispatcher-2 table exactly:

| Code | Arm | Live `X:$2D4A` | Effect |
|---|---|---:|---|
| 0 idle | `P:$2B04F` | −33 | bleed |
| 1 LKA sustained | `P:$2B031` | **+40** | **build** |
| 2 LKA transient | `P:$2B04E` | −9 | bleed |
| 3 idle | `P:$2B04F` | −33 | bleed |
| 4 LCA entry | `P:$2B031` | **+40** | **build** |
| 5 LCA sustained | `P:$2B04E` | **−9** | **bleed** |

## Part of the hypothesis is REFUTED

The route-8 write-up predicted that *both* dispatchers differentiate LCA. They
do not:

```text
rate_limit (X:$2D46):  1024 in BOTH modes  -- IDENTICAL
```

Dispatcher 1's scheduled-vs-flat split is **numerically neutral on this drive**.
`X:$2DA0` measured a constant 256 (Q8 unity), so the code-1 arm's
`(X:$08F4 * X:$2DA0) >> 8` reduces to exactly `X:$08F4`, and the flat arm
supplies the same 1024 rate limit anyway.

> **Retraction.** `lca_authority_limiter_analysis.md` §1 framed the two
> dispatchers as one coherent LCA-limiting design. Only **dispatcher 2**
> differentiates. Dispatcher 1's difference is real in code and inert in
> numbers — exactly the trap the same document warned about when it noted
> that "dynamic ≠ bigger".

The `rate_limit_source` (`X:$2D50`) prediction held: constant `1024` (`0x0400`)
throughout, in both modes, never varying — so the BV6T-vs-CV6T constant-source
difference stands, but it is not what starves LCA.

## The behavioural signature

Code-4 (LCA entry, +40) never lasts: **35 episodes, every one 0.0–0.1 s**.
Code 5 (−9) immediately follows. The state machine grants build authority for
a single cycle, then switches to bleed:

```text
4 -> 5: 28 transitions      5 -> 0: 29      0 -> 4: 22
```

Every multi-sample code-5 episode shows the integrator monotonically collapsing
to zero:

```text
ep48  [20, 25, 14, 14, 6, 5, 0]
ep51  [22, 22, 12, 13, 5, 6, 0, 0]
ep70  [5, 7, 0, 0]
ep72  [9, 0, 0, 0]
ep92  [6, 6, 4, 0]
```

Contrast the one sustained LKA episode, which holds real magnitude for seconds:

```text
integrator      505 [8..1923]
accumulator     372 [-720..1923]
```

LCA's accumulator never exceeds **28**, roughly **70x** smaller than LKA's
peak. That is precisely the reported symptom — the car wanders until LKA
kicks in.

## Caveats

- **LCA sample count is thin.** Only 7 usable samples across 2 episodes that
  cleared the 0.5 s threshold; the longest code-5 episode was 0.7 s. The
  constants are unambiguous (single distinct value each), but the *dynamics*
  deserve a longer centering run.
- **Code 3 dominates (74.5 %).** Labelled "idle" from the old static mapping;
  its true meaning is unverified and it was not exercised deliberately.
- This drive did not reproduce joint8's 39 s sustained request-6 episode. The
  availability handshake and the torque path are separate questions; joint9
  measured the latter.
- **Not established:** that flipping the sign would be safe or sufficient.
  `X:$08F6` is shared with code 2 (LKA transient), so it cannot be edited
  without affecting that state too.

## Consequence

The fix is no longer "raise a limit". The candidates are:

1. **Repoint code 5** in the dispatcher-2 table from `P:$2B04E` (bleed) to
   `P:$2B031` (build) — 1 word. Reuses the OEM scheduled arm that code 1 and
   code 4 already use, leaving all constants untouched. Cleanest.
2. **Change `X:$08F6`** from −9 to a positive value — but this is shared with
   code 2, so it perturbs LKA transients. Rejected unless (1) fails.

Option 1 is now the minimal change, and it is better founded than the earlier
proposal: the arm it redirects to is the same one **code 4 already runs during
LCA entry**, so the value is known to be live and correct in LCA context. The
earlier concern that `X:$2DA0` might be LKA-only is **resolved** — it measured
a constant 256 in every state including code 5.

Still requires a drive to validate, and the usual caution applies: this changes
control behaviour, unlike joint9.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_joint9_limiter.py --selftest      # 6 scenarios
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint9.csv
```
