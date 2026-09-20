# Joint9 — LCA authority-limiter measurement, test procedure

**Goal:** measure the per-state rate/authority limiters live and decide whether
sustained LCA (code 5) is granted less authority than sustained LKA (code 1).

**This is a read-only measurement.** The control path is byte-identical to the
joint8 image already driven; the only change is FD22 telemetry. Expect the car
to behave exactly as it did on the joint8 drive — no new steering behaviour.

---

## 0. Artifacts

```text
firmware  CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
sha256    30d9f4f632abfa1d81c8c4c6387d71631737499a8df9e7800b10b05ff97e4c6c
logger    work/lca_resume/fd22_joint9_limiter_logger.py   (FD22 format 6)
```

Fallback image if anything misbehaves: `CV6T-14C217-AR.VBF` (stock, flashed
twice on this vehicle already).

---

## 1. Bench — before going anywhere

```bash
cd /home/gl/Projects/ford/PSCM/Research

# rebuild and self-check (both are independent code paths)
python3 work/lca_resume/build_lca_joint9_limiter_vbf.py --selftest
python3 work/lca_resume/verify_lca_joint9_vbf_independent.py
python3 work/lca_resume/fd22_joint9_limiter_logger.py --selftest
python3 work/lca_resume/analyze_joint9_limiter.py --selftest

# container integrity via the flasher's own reader
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py verify /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
```

Required output: `SELFTEST: ALL PASS`, `ACCEPTANCE: READY TO FLASH`,
`SELFTEST PASS: format 6`, `SELFTEST PASS: 5 scenarios`, `ALL CRCs OK`.

Confirm the hash before flashing:

```bash
sha256sum /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
```

---

## 2. Flash (engine off, battery support recommended)

First a dry run — it resolves the target and SBL without touching the module:

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash --dry-run \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
```

Confirm it prints `TARGET PSCM ... tx=0x730 rx=0x738`, `integrity OK`, and
selects SBL `BV6T-14C220-AA.vbf`. Then flash for real:

```bash
python3 vbflasher.py flash \
    /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_LCA_JOINT9_LIMITER.VBF
```

The flasher prompts for confirmation before writing (answer `y`); add `--yes`
only if you want it unattended.

Then power-cycle the ignition and confirm the module answers and is clean:

```bash
python3 vbflasher.py ident PSCM
python3 vbflasher.py dtc PSCM
```

`dtc` should report no *actual* DTCs. Stored codes from previous experiments
are expected; clear them first if you want a clean baseline:

```bash
python3 vbflasher.py cleardtc PSCM
```

---

## 3. Static check — before driving

Engine running, car stationary, wheels straight. Confirm telemetry answers:

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_joint9_limiter_logger.py --count 20 --label static
```

**Acceptance:** rows show `status=ok` and `code=0 idle`. If every row is
`timeout` or `error`, the handler is not being reached — stop and reflash
stock rather than driving.

Note the `src=` value here. It should already read the constant (expected
`1024` = `0x0400`) even at standstill, because nothing recomputes it.

---

## 4. The drive — ONE file, drive both modes in any order

Record the whole drive into a **single log**. You do not need to switch files
or coordinate labels: every row carries `per_state_code`, so the analyzer
recovers LKA and LCA episodes from the data itself and ignores the label
column entirely. Interleave the modes however is convenient.

Route: the same multi-lane road used for joint7/joint8, steady 60–90 km/h.

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_joint9_limiter_logger.py -o fd22_joint9.csv
```

Leave it running for the whole drive and stop with Ctrl-C at the end. The CSV
is flushed every row, so an interrupted drive still yields usable data.

During the drive, produce **both** of these — order and repetition do not
matter, and more/longer episodes are better:

* **Sustained LKA (the positive control).** Drift toward a lane marking and
  let LKA nudge, hands lightly on the wheel. The live line shows
  `code= 1 LKA-sustained`. A few engagements of ≥1 s each is plenty.
* **Sustained LCA.** Enable lane centering and let it hold as long as it will
  (joint8 managed 39 s). The live line shows `code= 5 LCA-sustained`.

Both are required. If the drive contains only one of them the analyzer reports
INCONCLUSIVE rather than half a comparison — the LKA control is what makes the
LCA number interpretable.

> **Safety.** Keep hands on the wheel throughout. This image changes no
> control logic, but it retains the three joint4/joint8 bypasses, so LCA will
> behave as it did on the joint8 drive. Abort by steering normally.

### Optional — CAN cross-check

From a second terminal, for joint-analysis capability as in previous rounds:

```bash
candump -L can0 > fd22_joint9_can.log
```

---

## 5. Analysis — one command

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint9.csv
```

It splits the single file into episodes by `per_state_code`, discards 0.2 s at
each episode boundary (so mode-change blending cannot pollute a mean), drops
episodes shorter than 0.5 s as transition artefacts, pools the rest per mode,
and prints the comparison plus a verdict.

Sample output shape:

```text
sustained episodes (>= 0.5 s, 0.2 s guard each end):
   LKA code 1: 3 episode(s), 59 usable samples  [2.30, 3.00, 1.70]
   LCA code 5: 2 episode(s), 332 usable samples  [11.90, 21.90]

measure                               LKA code 1                LCA code 5
--------------------------------------------------------------------------
rate_limit_source                1024 (constant)           1024 (constant)
rate_limit                       4096 (constant)           1024 (constant)
authority_increment                  81 [79..84]             20 (constant)
```

### The four outcomes it distinguishes

| Verdict | Meaning |
|---|---|
| `HYPOTHESIS CONFIRMED` | LCA's `rate_limit` and/or `authority_increment` are below LKA's. The limiter is a real candidate cause; proceed to design a fix. |
| `HYPOTHESIS REFUTED` | LCA's limits equal or exceed LKA's. Not the binding constraint — look downstream of `X:$2D53`. |
| `REFUTED` (varying source) | `X:$2D50` moved during the drive. The static "literal constant" claim is wrong; retract it and hunt the indirect writer. |
| `INCONCLUSIVE` | One of the two modes is missing. Drive again; a one-sided log cannot be interpreted. |

It also runs a control: if `schedule_input` (`X:$2DA0`) is zero throughout
LKA, the code-1 arm is not actually scheduling and §2 of the analysis needs
re-examining. That prints as `CONTROL FAILED`.

Exit codes: `0` verdict reached, `2` inconclusive, `3` constant-source claim
refuted — so it can be scripted.

---

## 6. After the drive

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_joint9_limiter.py fd22_joint9.csv
python3 work/lca_resume/verify_authority_limiter.py     # 33/33, unchanged
wc -l fd22_joint9.csv
```

Check DTCs once more, since this is the first drive on a format-6 handler:

```bash
cd /home/gl/Projects/ford/VBFlasher && python3 vbflasher.py dtc PSCM
```

Then hand back `fd22_joint9.csv` (and the analyzer output) for review.

---

## 7. Rollback

At any point, to return the module to stock:

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR.VBF
python3 vbflasher.py cleardtc PSCM
```

---

## 8. Explicitly NOT in this test

No change to any limiter constant, dispatcher table, or arm. The tempting
2-word edit — repointing code 5 at the code-1 arms — is **deferred until this
measurement is in hand**, because the code-1 arm reads `X:$2DA0`, whose value
during an LCA episode is exactly what this drive measures. Redirecting code 5
there blind could produce zero authority or an unbounded step.
