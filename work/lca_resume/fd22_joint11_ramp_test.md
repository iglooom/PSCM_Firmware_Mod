# Joint11 — LCA ramp fix + demand telemetry

> **⚠ MORE STEERING AUTHORITY THAN JOINT10.**
> Joint10 gave a "thin feel". This patch removes what looks like the dominant
> gate, so the change should be considerably larger. Treat the first
> engagement as genuinely unknown. §3 and the abort criteria are not optional.

## 0. What changed and why

**New this round** — one word:

```text
dispatcher-1 code-5 entry   P:$2AFAE   AFE2 -> AFD8
```

**Kept from joint10:**

```text
dispatcher-2 code-5 entry   P:$2B02F   B04E -> B031
```

### The reasoning

The rate limiter at `P:$2AFEA` takes **two** inputs:

```text
F17C 2D46   B  = X:$2D46       the CLAMP
F47C 2D47   X0 = X:$2D47       current ramp
46C4 2D47   Y0 = Y0 + X:$2D47  apply the INCREMENT
D57C 2D47   X:$2D47 = Y0       store new ramp
```

Joint9 and joint10 instrumented the **clamp** (`X:$2D46`, measured 1024 in
both modes — equal, hence "inert"). They never instrumented the **increment**,
which is where the arms actually differ:

```text
code 1  P:$2AFB0   Y0 = divider result    (computed)
code 4  P:$2AFD8   Y0 = divider result    (computed)   <- LCA entry
code 5  P:$2AFE2   Y0 = 0    (E580)       <- THE DEFECT
```

The demand is built at `P:$2B054`:

```text
X:$2D49 = (X:$2D54 * X:$2D47) >> 10
           control-law    ramp
```

With a zero increment the ramp `X:$2D47` can never grow, so the demand stays
near zero **regardless of what the control law produces**. That gates the
entire LCA demand and explains joint10's ~50x shortfall: the limiter was slack
(max step 17 of 40) precisely because there was nothing to limit.

The fix points code 5 at the code-4 arm. That arm writes the **same clamp**
(`X:$2D46 = X:$2D50`), so the bound is unchanged — the only difference is a
computed increment instead of a literal zero. Code 4 runs during every LCA
entry, so the arm is proven live in LCA context.

> This also **corrects joint10's analysis**, which called dispatcher 1
> "numerically inert". That was true of the clamp and false of the arm: the
> two arms differ on a word that was never measured.

```text
firmware  CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF
sha256    ab39234a295da0da21b9ad59c585354e8aab67bdb8858eeff561c5dfa99a82a6
```

Telemetry moves to **FD22 format 7**, retargeted at the demand chain:

```text
X:$2DB9 code   X:$2D47 ramp   X:$2D54 control_law   X:$2D49 demand
X:$2D46 clamp  X:$2D4B integrator   X:$2D53 accumulator
```

---

## 1. Bench

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/build_lca_joint11_ramp_vbf.py --selftest
python3 work/lca_resume/verify_lca_joint11_vbf_independent.py
python3 work/lca_resume/fd22_joint11_demand_logger.py --selftest
python3 work/lca_resume/analyze_joint11_demand.py --selftest

cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py verify /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF
```

Required: `SELFTEST: ALL PASS`, `ACCEPTANCE: READY TO FLASH`,
`SELFTEST PASS: format 7`, `SELFTEST PASS: 5 scenarios`, `ALL CRCs OK`.

The verifier proves **both** dispatch tables have only their code-5 entry
moved, all twelve arms are byte-identical to stock, and the zero-increment arm
still exists untouched (codes 0/2/3 continue to use it).

---

## 2. Flash

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash --dry-run \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF
python3 vbflasher.py flash \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF
```

Power-cycle, then `python3 vbflasher.py dtc PSCM`. Any actual steering DTC →
do not drive, roll back (§7).

---

## 3. Stationary check — MANDATORY

Engine running, stationary, open area, **hands on the wheel**.

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_joint11_demand_logger.py --count 40 --label static11
```

Expect `status=ok`, `code=0` or `3`, **wheel still**, no lamps. LCA cannot
engage below the speed threshold, so this only proves a healthy boot.

---

## 4. First drive — treat as unknown

**Empty, straight, wide road. Both hands on the wheel. Low speed first.**

```bash
python3 work/lca_resume/fd22_joint11_demand_logger.py -o fd22_joint11.csv
```

The live line now shows `ramp=` and `dmd=`. The single number that tells you
the patch engaged: **`ramp` rising during `code= 5`**, instead of sitting at 0.

Let LCA engage **briefly** at first and be ready to override.

**Abort — reflash joint10 or stock if any of:**

* the wheel pulls harder than you expect, or fights an override
* oscillation / weaving / hunting around the lane centre
* any steering DTC
* anything that surprises you

If the first engagement is calm, extend to longer episodes and normal speed,
and give the analyzer several multi-second code-5 episodes plus sustained LKA
as the control.

---

## 5. Analysis

```bash
python3 work/lca_resume/analyze_joint11_demand.py fd22_joint11.csv
```

It answers three questions in order:

1. **Did the ramp unblock?** `LCA ramp now VARIES` = the fix engaged.
   `STILL ZERO` = it did not; stop and investigate before patching further.
2. **Does the demand model hold?** It checks `demand == (control_law * ramp) >> 10`
   sample by sample. `MODEL REFUTED` means our reading of `P:$2B054` is wrong
   and the columns cannot be trusted — a real possibility worth catching.
3. **Is torque comparable to LKA?** Reported as a ratio, plus the gain versus
   joint10's LCA peak of 54.

If the ramp moves but torque stays small, the remaining constraint is
`X:$2D54` (the control-law output) and the analyzer says so.

---

## 6. After the drive

```bash
cd /home/gl/Projects/ford/VBFlasher && python3 vbflasher.py dtc PSCM
```

Report the CSV, the analyzer output, and **how it felt** — the subjective
judgement is still the acceptance criterion.

---

## 7. Rollback

```bash
cd /home/gl/Projects/ford/VBFlasher
# back to joint10 (thin but safe, known-driven)
python3 vbflasher.py flash \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT10_FIX.VBF
# or fully stock
python3 vbflasher.py flash /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR.VBF
python3 vbflasher.py cleardtc PSCM
```

---

## 8. If it is too strong

Do **not** revert the redirect — that returns to zero ramp. The correct knob is
the code-4 arm's numerator `X:$03B0`, which feeds the divider that produces the
increment:

```text
P:$2AFD8  8745 0400        C1 = 0x400        denominator
          F77C 03B0        Y1 = X:$03B0      numerator  <- the knob
          E254 011F        JSR divider
```

`X:$03B0` is read-only calibration, currently unmeasured. Note it is shared
with code 4 (LCA entry) only — **not** with LKA — so lowering it affects LCA
alone. That makes it a clean tuning parameter if the authority overshoots.
