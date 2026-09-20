# PSCM Research — Ford electric power steering (CAN 0x730)

MCU: **Freescale MC56F8366** (DSP56800E, 16-bit, word-addressed).
Module: `F188 = CV6T-14C217-AR`, `F124 = CV6T-14C218-AX`, VIN `WF0AXXWPMAEL32600`.

## Status

| Goal | State |
|---|---|
| Read program flash (512 KB) | **DONE** — byte-exact vs OEM VBF |
| Read data flash (32 KB) | **DONE** — byte-exact vs OEM VBF |
| Read boot flash / PBL (32 KB) | blocked by the SBL's address policy |
| Read external EEPROM (1 KB) | open — separate SPI device, not in flash |
| Write modified blk0/blk1 | ✅ **UNBLOCKED** — `14C217` blk1 word A **solved** (CRC-16/MCRF4XX over `blk0 ++ blk1[START:0x63FEA)`; `START` is per build). See `PSCM_internal_checksums.md` §6 and §10 |
| **Enable Lane Centering Aid (LCA)** | ✅ **DONE** — 5 words, flashed and driven; LCA now steers. See `PSCM_LCA_enabler.md` |

## Lane Centering Aid enabler

Stock `CV6T-14C217-AR` displays LCA and accepts the camera's command but
applies no usable steering torque. Five word-sized edits fix it; no calibration
constant is changed, and each edit redirects execution to an OEM code path the
module already runs.

Root cause: sustained LCA runs per-state code **5**, which both per-state
dispatchers treat as the *transient* member of the LCA group — an arm whose
ramp increment is a literal `0` and whose authority increment is `−9` (a decay
term). LKA is unaffected: it spends 88–90 % of engaged time in its **active**
slot, while LCA spent 98.9 % in the transient one.

```text
CV6T-14C217-AR_LCA_ENABLED.VBF
sha256 24a9b235920e0eb54eb233445577630d2846574efb556a9aad74c5e207f49999
22 bytes differ from stock: 9 patch words + 2 internal checksum words
```

Measured on the vehicle: peak LCA torque authority went from 28 (stock) to
2000, comparable to LKA's 1676.

```bash
python3 work/lca_resume/build_lca_final_vbf.py --selftest
python3 work/lca_resume/build_lca_final_vbf.py
python3 work/lca_resume/verify_lca_final_vbf_independent.py   # 43/43, separate code path
```

**Known limitation, not a PSCM bug:** lane assist stops steering after ~3.7 s
and re-arms. That cap is imposed by the IPMA (camera) while the PSCM is still
granting permission, and cannot be changed from `14C217`.

## Documents

| File | Contents |
|---|---|
| **`PSCM_LCA_enabler.md`** | **The LCA fix.** Firmware layout, what each cell does, the five patch offsets, checksum repair, flashing. Self-contained — no investigation narrative. |
| `PSCM_internal_checksums.md` | The ECU's internal integrity layers. Required reading before any *write*. |
| `PSCM_preflight.md` | What must be settled before touching the module; all blockers now cleared. |
| `PSCM_eeprom_read_write_guide.md` | Reading and writing PSCM EEPROM with the OEM SBL — no custom bootloader needed. |
| `PSCM_ucds_eeprom_procedure.md` | UCDS EEPROM read/write procedure analysis and SBL recovery, from third-party captures. |

### Lane-assist analysis (how the fix was found)

| File | Contents |
|---|---|
| `PSCM_LCA_investigation.md` | Master record: routes closed, retractions, live measurements. Read only if extending the work. |
| `LCA_BRIEF.md` | Short shared briefing on the LCA question. |
| `PSCM_BV6T_vs_CV6T_lane_assist.md` | Does BV6T support LCA, and how does it differ? |
| `PSCM_structural_compare_report.md` | Compile-invariant structural comparison, BV6T-AF vs CV6T-AR with CV6T-AH as control. |
| `PSCM_14C218_calibration_compare.md` | Calibration-block comparison, BV6T vs CV6T. |
| `PSCM_config_route_and_partnumbers.md` | Whether LCA could be enabled by configuration rather than firmware. |
| `PSCM_LCA_gate.md` | An earlier gate hypothesis — **REFUTED**, kept so the dead end stays visible. |
| `PSCM_can_id_handling.md` | Where `0xA5` lives; CAN ID map across the three images. |
| `PSCM_DID_map.md` | DID map with UCDS names bound to real identifiers. |
| `PSCM_vehicle_test_plan.md` | Vehicle test plan for the LKA/LCA drives. |
| `DRIVE_RESULTS.md` | First instrumented drive, 141 097 decoded samples. |

## Quick start

```bash
python3 work/pscm_preflight.py --execute --iface can0        # read-only identity
python3 work/pscm_dump.py      --execute --iface can0 --full # dump everything
python3 work/verify_dumps.py                                 # offline proof
```

Vehicle stationary, ignition on, engine off, stable supply, `can0` qdisc
`pfifo_fast`. Power-cycle the module afterwards.

## Regression tests (run before changing transfer logic)

```bash
python3 work/test_upload_counter.py    # counter wrap 0xFF->0x00, 37-on-failure
python3 work/test_preflight_logic.py   # NRC decode, version matching
python3 work/dsp56800e_dis.py 0 1      # disassembler self-test (JSR 55/55)
```

Before flashing an LCA image:

```bash
python3 work/lca_resume/build_lca_final_vbf.py --selftest
python3 work/lca_resume/verify_lca_final_vbf_independent.py
python3 work/lca_resume/verify_enabler_doc.py   # doc offsets vs the binaries
```

## Safety

* Reading writes **nothing**: no FM register, no erase, no flash program.
* The SBL runs from volatile **RAM**; a power cycle always restores the module.
* Flashing a modified `14C217` blk0/blk1 is now possible, but **recompute blk1
  word A first** (`python3 work/wordA/wordA_solved.py`). Its `START` constant is
  **per build** — read it from the image, never assume. The check runs as a
  continuous background monitor, so a wrong value faults the module while
  driving, not just at boot (`PSCM_internal_checksums.md` §6, §10).
* `F111` is the *hardware* part and never matches a VBF `sw_part_number`.
  Gate on `F188` (EXE) / `F124` (DATA).

### Flashing the LCA enabler

This is a **steering** module and the LCA image changes steering behaviour.

* Keep the stock `CV6T-14C217-AR.VBF` — it is the rollback path and reflashes
  cleanly. Reverting is a normal flash.
* Assert every expected stock value before writing. The build scripts here do;
  a layout mismatch that gets silently patched produces arbitrary behaviour.
* Verify with the independent checker before flashing, not just the builder.
* First drive: empty straight road, both hands on the wheel, brief engagement,
  ready to override. Abort on oscillation, on torque that fights an override,
  or on any steering DTC.
* If LCA feels too strong, do **not** revert the ramp patch — that restores a
  zero ramp. Tune `X:$03B0`, which is shared with LCA entry only, never with
  LKA (`PSCM_LCA_enabler.md` §7).
