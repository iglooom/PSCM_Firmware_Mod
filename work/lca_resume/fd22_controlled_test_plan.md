# CV6T LCA — next decisive controlled test

## Objective

Locate the first runtime stage at which working LKA (`per_state_code = 2`) and ineffective sustained LCA (`per_state_code = 5`) diverge. Do not change torque, lane state, calibration, learned state, or actuator commands during this test.

## Why this is next

Static analysis has already excluded the obvious direct cruise gate, separate final enable arm, selector-zero hypothesis, and several code-5-specific attenuation hypotheses. EEPROM analysis proved persistent friction statistics but has not produced an LCA-specific consumer.

The remaining split is runtime-only:

1. LCA command is already absent/small before the common torque path; or
2. it reaches the common path and is limited/cancelled downstream; or
3. torque demand exists but motor current/physical authority does not follow.

The observation-only FD22 image measures all three boundaries in one response.

## Artifact gate

Instrumented file:

```text
CV6T-14C217-AR_FD22_SNAPSHOT.VBF
SHA-256 d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370
```

Required live identities before flashing:

```text
F188 = CV6T-14C217-AR
F124 = CV6T-14C218-AX
```

Do not flash if either differs. The repaired application checksum was computed with AX calibration.

Offline verification, rerun immediately before the test:

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/verify_fd22_snapshot_vbf_independent.py
python3 /home/gl/Projects/ford/VBFlasher/vbflasher.py verify \
  CV6T-14C217-AR_FD22_SNAPSHOT.VBF
sha256sum CV6T-14C217-AR_FD22_SNAPSHOT.VBF
```

Required terminal results:

```text
READY FOR CONTROLLED TEST
ALL CRCs OK
d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370
```

## Pre-flash checks

Vehicle stationary, stable external power supply connected, stock recovery VBF immediately available, and no computer interaction by the driver during any road test.

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py ident PSCM --iface can0
python3 vbflasher.py dtc PSCM --iface can0
```

Save both outputs. Confirm F188/F124 manually.

Dry-run plan. Substitute the exact live F111 returned by `ident`; dry-run intentionally does not connect to the ECU:

```bash
HW='BV6T-14C262-AA'  # use only if this exactly matches the fresh live F111
python3 vbflasher.py flash --dry-run --hw "$HW" \
  /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_FD22_SNAPSHOT.VBF
```

The plan must select PSCM `0x730/0x738`, the correct SBL for that F111, three erase regions, three data blocks, finalise `31 01 0304`, and reset `11 01`.

## Controlled flash

Run without `--yes`; retain the interactive confirmation as the final human gate:

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py flash --iface can0 \
  --logfile /home/gl/Projects/ford/PSCM/Research/fd22_flash.log \
  /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_FD22_SNAPSHOT.VBF
```

Do not interrupt after erase begins. If any unexpected identity, checksum, SBL, erase, transfer, finalise, or reset response occurs, preserve the logfile and stop rather than retrying blindly.

## Parked validation before driving

After the application has restarted:

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_snapshot_logger.py \
  --iface can0 --rate 10 --count 100 --label parked \
  --output fd22_parked.csv

python3 /home/gl/Projects/ford/VBFlasher/vbflasher.py ident PSCM --iface can0
python3 /home/gl/Projects/ford/VBFlasher/vbflasher.py dtc PSCM --iface can0
```

Acceptance criteria:

- 100 valid `62 FD 22` responses or a clearly understood small number of bus timeouts;
- format byte `0` on every valid response;
- F188/F124 unchanged;
- no new actual PSCM DTC;
- ordinary steering behavior unchanged while parked.

Do not road-test if these checks fail.

## Road capture

Use a passenger or start unattended logging before moving. Do not operate the computer while driving.

One continuous capture is preferable because the internal state and code identify phases without manual relabelling:

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/fd22_snapshot_logger.py \
  --iface can0 --rate 10 --label mixed-road \
  --output fd22_mixed_road.csv
```

Capture, under comparable speed and road conditions:

1. idle/no lane intervention;
2. several physically effective ordinary LKA corrections (`code 2`);
3. several sustained LCA episodes (`code 5`), including episodes long enough to avoid transition-only samples.

Prefer repeated A/B episodes on the same road and speed range. Preserve the raw CSV even if the drive seems uneventful.

## Fields and decision table

| Field | Runtime cell | Diagnostic meaning |
|---|---:|---|
| lane state | `X:$2DDE` | confirms internal state |
| per-state code | `X:$2DB9` | separates LKA `2` from LCA `5` |
| upstream intermediate | `X:$2D54` | command before final multiply/function path |
| torque accumulator | `X:$2D53` | common accumulated lane-assist demand |
| torque output/result | `X:$2D52` | torque-function result |
| FD0E source | `X:$1CB1` | torque-loop demand |
| FD0C source | `X:$171B` | Q-axis motor current |

Interpret sustained code-2 versus sustained code-5 samples:

| Observation during code 5 | First divergent stage | Next research/patch target |
|---|---|---|
| `2D54` is zero or much smaller than code 2 | upstream extraction/scaling/command production | trace producers of `2D54`; correct LCA source or scaling |
| `2D54` is comparable, but `2D53` collapses | accumulation, ramp, limiter, or cancellation | trace the exact limiter/cancellation input |
| `2D53` is comparable, but `2D52` or FD0E collapses | torque function/downstream demand gate | trace torque-function output and downstream gate |
| FD0E is comparable, but FD0C collapses | current-loop or actuator acceptance | investigate motor-current request/acceptance path |
| FD0E and FD0C are both nonzero but much smaller | authority/scale issue | quantify ratio; adjust only the proven LCA-specific gain with stock clamps retained |
| FD0E and FD0C are comparable to LKA | sign/cancellation or physical interpretation | compare signs, steering direction, and actual wheel response |

Use medians, signed ranges, and per-episode plots; do not compare isolated transition samples.

## Only after localization

Build an enabling patch only after one row of the decision table is supported by the capture. Keep the first behavioral patch minimal and LCA-specific, preserve OEM saturation/fault handling, verify all internal/container checksums, and test parked/bench before any closed-course test.

EEPROM modification, lane-state forcing, and raw torque injection are not justified before this observation capture.

## Recovery

Keep the verified stock `CV6T-14C217-AR.VBF` available. Restore stock using the already proven PSCM flashing path after the capture or immediately if any unexpected behavior or DTC appears. Verify identity and actual DTC state after restoration.
