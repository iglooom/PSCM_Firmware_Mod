# PSCM — what to settle BEFORE attempting a backup

Answer to "what else should we do first". Ordered by *what can invalidate the
whole effort*, not by effort. Items 1–3 are blockers; 4–6 are risk reduction.

---

## 1. BLOCKER — we do not know which firmware the module runs

We hold **three** different 14C217 application versions:

| VBF | Build | Held |
|---|---|---|
| `BV6T-14C217-AF` | 2011-08-23 | yes |
| `CV6T-14C217-AH` | 2013-01-15 | yes |
| `CV6T-14C217-AR` | 2016-06-22 | yes |

The car's EEPROM says assembly `CV61-3C579-AL`, config `CV6C-3D070-LF`, VIN
`WF0AXXWPMAEL32600` — **none of which is the software part number.** The
software part is DID `F188`, which we have never read.

Why this blocks everything: the planned acceptance test is "dump a small region
and compare byte-exact against the OEM VBF at the same address". That test has
**no reference** until we know which VBF is on the module. Worse, comparing
against the *wrong* version would produce a mismatch that looks like a broken
reader, or — if the region happens to be identical across versions — a false
PASS.

**Action:** read `22 F188` (and `F124` for the calibration). If it returns a
version we do not hold, obtain that VBF before dumping, or the dump cannot be
validated at all.

---

## 2. BLOCKER — the SecurityAccess constant is unverified

`0x9B2533` comes from a third-party table (VBFlasher), not from our own capture.
The algorithm is proven (§4 of the feasibility doc), but the *constant* is not.

**Action:** `27 01` then `27 02` in the default session. Unlocking grants rights
and modifies no memory, so this is a safe read-only test. `NRC 0x35 invalidKey`
⇒ the constant is wrong for this module/level and must be re-derived from a
genuine seed/key pair before any SBL work is worth doing.

---

## 3. BLOCKER — has anyone checked whether the APPLICATION can already read?

All the read-capability analysis so far has been about the **SBL**. But the
application is a different program with a different service set, and Ford's own
CANdela DB lists `0x22 ReadDataByIdentifier` and `0x19` in the *default* and
*extended* sessions.

If the application implements `0x23 ReadMemoryByAddress`, **the entire custom-SBL
effort is unnecessary.** The 2025 Transit DB says it does not — but that is a
different ECU generation, and it costs one request to find out for this one.

**Action:** probe `23` and `35` in the extended session. A negative response is a
perfectly good answer.

---

## 4. The EEPROM changes 14.8% between reads — verification must account for it

Measured across the three dumps of this car (`work/eeprom_delta.py`):
**152 of 1024 bytes differ** between healthy reads of the same module.

```
0x1BC..0x1C8   0x1DC..0x1EA   0x1FE..0x1FF   0x22A..0x233   0x238..0x23D
0x242..0x249   0x254..0x263   0x26E..0x27D   0x288..0x289   0x292..0x297
0x2B8..0x2BC   0x2C8..0x30C   0x352..0x397
```

The identity region `0x000..0x060` (part numbers + VIN) is **byte-identical in
all three**.

Consequence: **"dump twice and compare" is not a valid pass/fail test for the
EEPROM.** A verifier that diffs whole files would report a false FAIL every
single time. It must require the static region to match exactly and report —
but tolerate — differences in the volatile offsets. Those regions look like
adaptation/counters (the `0x2C8` and `0x352` blocks carry repeating 4-byte
records that grow monotonically).

Note the flash dump has no such problem: program flash is static between reads,
so byte-exact comparison IS the right test there.

---

## 5. Transport and environment

* `can_isotp`, `can_raw`, `can_dev` are loaded; `candump/cansend/cangw` present.
  No CAN interface is currently up — adapter not attached.
* The BCM flasher refuses to run unless the interface qdisc is `pfifo_fast`
  (`tccheck` in VBFlasher does the same). A wrong qdisc reorders frames and can
  corrupt a transfer. Check before any load.
* ISO-TP padding: Ford uses TX+RX padding with `0x00`. The BCM `uds.py` sets
  `CAN_ISOTP_TX_PADDING|CAN_ISOTP_RX_PADDING`; reuse it rather than re-deriving.
* Tester `0x730` → ECU `0x738` (VBF `ecu_address` + 8).

---

## 6. Power and physical risk

This is an **electric power steering** module. Two specifics:

* Do the first attempt with the car **stationary, engine off, ignition on** —
  never while driving, obviously, but also not mid-drive-cycle where a steering
  fault could latch DTCs that need clearing.
* A brown-out mid-transfer is the classic way to lose a module. Bench work
  should use a stable supply; on-car work should have the battery on a charger.
* Losing the OEM SBL from RAM is **harmless** (RAM is volatile, a power cycle
  restores normal PBL+application operation). Our reader writes no FM register,
  so dumping itself cannot brick the module.

---

## Ready-to-run

**STATUS: preflight COMPLETE (2026-09-13). All three blockers cleared.**

| # | Blocker | Result |
|---|---|---|
| 1 | Which firmware? | **`CV6T-14C217-AR` + `CV6T-14C218-AX` — both held, both verify clean.** Reference images for dump validation are in hand. |
| 2 | Security constant | **`0x9B2533` CORRECT.** seed `000008` → key `EB9D7C`, accepted `67 02`. |
| 3 | Can the application read? | **No.** `23`/`35` → NRC 11 serviceNotSupported in extended session. |

Item 4 (EEPROM volatility) stands as a verification-design constraint.
Item 5: transport works — full ISO-TP request/response exchange confirmed.
Item 6 (power/physical) still applies to every subsequent run.

Note `F188` returned `CV6T-14C217-**AR**`, the 2016 build — the newest of the
three we hold. `F111` hardware is `BV6T-14C262-AA`, a BV6T-era assembly running
CV6T-era software; that mix is normal and is exactly why the hardware DID must
never be used to gate a software match (the BCM project hit this and refused a
valid flash).

`work/pscm_preflight.py` remains re-runnable and is still read-only.

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/pscm_preflight.py --execute --iface can0
```

### Next: `work/load_and_probe_sbl.py`

Loads the **OEM** SBL into RAM and interrogates it. Settles two things at once:
whether the OEM SBL itself can read (which static analysis could not determine —
its dispatcher is table-driven), and whether our delivery path works using
known-good Ford code before we ever hand-assemble our own.

Writes **RAM only** (`P:$04F800`, documented on-chip Program RAM). Sends no
erase, no flash write, no reset. Recovery from anything is a power cycle.
