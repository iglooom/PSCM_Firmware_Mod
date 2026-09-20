# Joint10 — LCA authority fix, test procedure

> **⚠ THIS IMAGE CHANGES STEERING BEHAVIOUR.**
> Joint9 was telemetry-only and drove like stock. This one does not. Sustained
> lane centering will apply torque it has never applied on this car. Read §3
> before driving; the stationary check is not optional.

## 0. What changed and why

One word:

```text
dispatcher-2 table, code-5 entry   P:$2B02F   B04E -> B031
```

Joint9 measured the per-state authority increment `X:$2D4A` live:

| Code | Arm | `X:$2D4A` | Effect |
|---|---|---:|---|
| 1 LKA sustained | `P:$2B031` | **+40** | builds torque |
| 4 LCA entry | `P:$2B031` | **+40** | builds torque |
| 5 LCA sustained | `P:$2B04E` | **−9** | **bleeds torque to zero** |

A negative increment is a decay term, so sustained LCA could never hold
torque. This patch points code 5 at the **same arm codes 1 and 4 already use**
— including code 4, which runs during every LCA entry, so the arm is proven
live and correct in LCA context.

No constant was edited. In particular `X:$08F6` (the −9 cell) is untouched, so
**code 2 — LKA transient — is unaffected**.

```text
firmware  CV6T-14C217-AR_LCA_JOINT10_FIX.VBF
sha256    c41065a3d2a742d67bce25b456b404431282b6b5e4e4574463fcd0d7d0d74784
```

Telemetry is identical to joint9 (FD22 format 6), so the same logger and
analyzer work unchanged and the two drives are directly comparable.

---

## 1. Bench

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/build_lca_joint10_fix_vbf.py --selftest
python3 work/lca_resume/verify_lca_joint10_vbf_independent.py
python3 work/lca_resume/fd22_joint9_limiter_logger.py --selftest
python3 work/lca_resume/analyze_joint9_limiter.py --selftest

cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py verify /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT10_FIX.VBF
```

Required: `SELFTEST: ALL PASS`, `ACCEPTANCE: READY TO FLASH`,
`SELFTEST PASS: format 6`, `SELFTEST PASS: 6 scenarios`, `ALL CRCs OK`.

The independent verifier proves codes 0–4 dispatch is **unchanged** and both
arms plus the torque function are byte-identical to stock.

---

## 2. Flash

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash --dry-run \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT10_FIX.VBF
python3 vbflasher.py flash \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT10_FIX.VBF
```

Power-cycle the ignition, then:

```bash
python3 vbflasher.py ident PSCM
python3 vbflasher.py dtc PSCM
```

No *actual* DTCs expected. If the PSCM reports a steering fault here, **do not
drive** — reflash stock (§7).

---

## 3. Stationary check — MANDATORY before driving

Engine running, **car stationary, in an open area, hands on the wheel.**

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_joint9_limiter_logger.py --count 40 --label static10
```

Watch for:

* rows are `status=ok`, `code=0 idle` or `code=3`
* **the wheel does not move or fight you**
* no warning lamps

The car is stationary and below any lane-assist speed threshold, so LCA cannot
engage here. This step confirms the module booted healthy with the new image —
it does **not** exercise the patch.

If the wheel twitches, self-steers, or the module throws a DTC: reflash stock
immediately.

---

## 4. First drive — low speed, low commitment

Pick an **empty, straight, wide road**. Modest speed first (just above the
lane-assist engage threshold, ~45 km/h per drive3). **Both hands on the
wheel.**

```bash
python3 work/lca_resume/fd22_joint9_limiter_logger.py -o fd22_joint10.csv
```

Let lane centering engage **briefly** and be ready to override. What you are
checking, in order:

1. **Does it steer at all?** The whole point. Previously LCA applied nothing
   perceptible.
2. **Is it smooth or abrupt?** The +40 arm builds torque roughly 4.4x faster
   than the idle bleed removes it. The rate limiter (`X:$2D46` = 1024) is
   unchanged and still bounds how fast the command can move, but this is the
   first time it has been exercised in sustained LCA.
3. **Does it oscillate?** Weaving or hunting around the lane centre means the
   authority is now too high for the control loop's tuning.
4. **Does it release cleanly** when you override or cancel?

**Abort criteria — reflash stock if any occur:** oscillation, torque that
fights a deliberate override, any steering DTC, or any behaviour that
surprises you. A one-word change to a steering ECU deserves a low threshold
for stopping.

If the first engagement is calm, extend to longer episodes and normal speeds,
and drive enough sustained LCA to give the analyzer several multi-second
code-5 episodes. Also produce some sustained LKA as the control, exactly as in
joint9.

---

## 5. Analysis

```bash
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint10.csv
```

Expected if the fix works:

```text
authority_increment       LKA +40        LCA +40      <- was -9
integrator / accumulator  LCA magnitudes now comparable to LKA
```

The verdict line will now read `HYPOTHESIS REFUTED` — because the analyzer
tests whether *LCA is limited below LKA*, and after a successful fix it no
longer is. **That is the desired outcome for joint10.** The tool was written
for joint9's question; do not misread its label.

Direct comparison against the pre-fix drive:

```bash
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint9.csv   # -9, acc max 28
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint10.csv  # +40, acc ?
```

The number that matters is the **accumulator magnitude during sustained code
5**: joint9 never exceeded 28 against LKA's 1923.

---

## 6. After the drive

```bash
cd /home/gl/Projects/ford/VBFlasher && python3 vbflasher.py dtc PSCM
```

Then report back: the CSV, the analyzer output, and — most importantly — **what
it felt like**. The subjective judgement is the acceptance criterion here; the
telemetry only explains it.

---

## 7. Rollback

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR.VBF
python3 vbflasher.py cleardtc PSCM
```

Intermediate fallback — joint9, which drives like stock but keeps telemetry:

```bash
python3 vbflasher.py flash \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
```

---

## 8. If it is too aggressive

> **REFUTED BY THE JOINT10 DRIVE — do not build this patch.**
> The advice below assumed the +40 limit would be reached. It is not: in
> sustained LCA the measured per-cycle step never exceeded 17 of the 40
> allowed (mean 1.9), so the limiter is completely slack and changing
> `X:$08F4` is provably a no-op for LCA. It would only weaken LKA, which does
> hit the bound 80.5 % of the time. See `fd22_joint10_fix_analysis.md`.
> The real constraint is upstream: `X:$2D49 = (X:$2D54 * X:$2D47) >> 10`.
> Retained below for provenance.

Do not reach for the dispatcher table again. The tuning knob is `X:$08F4`
(measured **+40**), which the code-1/4 arm scales by `X:$2DA0` (measured 256 =
Q8 unity). Lowering it would weaken LKA too, since they share the arm — so a
proper fix would need a **separate constant for code 5**, i.e. a new arm rather
than a redirect. That is a larger change and should only be designed once this
drive says whether +40 is in the right range at all.
