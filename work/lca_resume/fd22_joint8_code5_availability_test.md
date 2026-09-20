# Joint8 — code-5 availability branch experiment

## Purpose

Test the proven feedback-loop cause without globally forcing the transmitted
availability value. The image removes only per-state code `5` from the
availability-degrading predicate while retaining all other known safety and
control logic.

## Exact application edit

```text
P:$2B8B0
stock: A303  Beq P:$2B8B4
new:   E700  NOP
```

For code `5`, execution now falls through to the retained `CMP #3` and selects
`A=0`. Code `2` and code `3` still select `A=1`. Auxiliary predicate
`X:$2DBA` remains unchanged.

The image also retains the two previously proven LCA entry patches:

```text
P:$2A780..P:$2A783  request-6 dispatcher gate: four NOPs
P:$2ABFB..P:$2ABFD  phase-1 X:$2DC1 entry test: three NOPs
```

No torque, actuator source, saturation, limiter, fault path, final payload, or
runtime state cell is directly modified.

## Artifact

```text
CV6T-14C217-AR_LCA_CODE5_AVAIL_FD22.VBF
SHA-256: 40b35b3862792bd8a48caa04bc4fbff27bde3e9b456002ec12d502e64ff63cce
source EXE SHA-256: cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5
required calibration SHA-256: 6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7
internal checksum A/B: 688D / 7969
block-1 CRC-16: EDA4
VBF file checksum: 513267C0
```

FD22 remains format 5:

```text
X:$2DDE  lane state
X:$2DB9  per-state code
X:$2D53  torque accumulator
X:$2DAF  state-machine phase
X:$2D28  availability enum
X:$2252  availability mirror
X:$2DBA  auxiliary availability predicate
```

## Static verification

```text
Builder tests:                      2/2 PASS
Related LCA builder regression:     6/6 PASS total
Builder deterministic self-test:    PASS
Independent verifier:               PASS
Python compilation:                 PASS
vbftool container verification:     PASS
VBFlasher verification:             PASS
VBFlasher dry-run:                  PASS
```

Diff from exact stock:

```text
6 clusters, 141 changed payload bytes
0x01DD48..0x01DD4B  FD22 pointer
0x054F00..0x054F08  dispatcher bypass
0x0557F6..0x0557FC  phase-entry bypass
0x057160..0x057162  code-5 branch A303 -> E700
0x067000..0x067077  format-5 observation handler
0x07FFEA..0x07FFEE  internal checksums A/B
```

All changed bytes are classified. All other application blocks and all
neighboring availability-predicate instructions are unchanged.

## Controlled test procedure

Before flashing, stationary with stable external power, require:

```text
F188 = CV6T-14C217-AR
F124 = CV6T-14C218-AX
```

Keep the verified stock `CV6T-14C217-AR.VBF` available for recovery. Use the
normal interactive confirmation; do not use `--yes`.

After programming and reset, remain parked and check:

1. identities still match;
2. steering initializes normally and has no abnormal assistance;
3. FD22 format-5 responses are valid;
4. no new actual PSCM DTC is present;
5. conventional LKA status/availability remains normal.

Do not proceed to a driving test if any parked check fails.

For a passenger-operated or unattended joint capture:

```bash
python3 work/vehicle/la_monitor.py --iface can0 --log fd22_joint8_can
python3 work/lca_resume/fd22_availability_producer_logger.py \
  --iface can0 --rate 10 --label joint8 --output fd22_joint8_internal.csv
```

Acceptance requires simultaneous evidence:

- request `6` remains sustained instead of changing to `7` after phase 6;
- phase `6` / code `5` retains `X:$2D28 = X:$2252 = 3` while `X:$2DBA=0`;
- raw frame `0x140` availability remains `3`;
- torque accumulator is nonzero during active control;
- no new actual PSCM DTC or abnormal steering behavior occurs;
- request `2`/`4` conventional behavior and non-LCA availability states remain
  unchanged.

This artifact is statically verified but not yet validated on the ECU.
