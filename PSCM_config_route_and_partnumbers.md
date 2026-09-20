# PSCM — non-firmware (configuration) route to LCA, and part-number evidence

Scope: can Lane Centering Aid be enabled on this PSCM by As-Built / UDS
configuration rather than by patching 14C217? And is the CV6T-software PSCM
hardware even LCA-capable?

Read-only desk study. No vehicle communication was performed.

---

## A. Vehicle identity (what the module actually reports)

From `/home/gl/Projects/ford/09042026_WF0AXXWPMAEL32600.uuw`
(UCDS read of the user's own car, VIN `WF0AXXWPMAEL32600`, `VEHICLE_ID=C520_EU`,
`VEHICLE_YEAR=MY17`), node 0x730:

| DID | Meaning | Value |
|---|---|---|
| F111 | ECU Core Assembly (hardware) | **BV6T-14C262-AA** |
| F113 | ECU Delivery Assembly | CV61-3C579-AL |
| F188 | Vehicle Manufacturer ECU Software | **CV6T-14C217-AR** |
| F124 | Calibration Data #1 | **CV6T-14C218-AX** |
| F108 | Network Signal Calibration | CV6T-14C386-AB |
| F110 | Diagnostic DB reference | DS-AV6C-3C579-AA |
| F162 / F163 | SW download spec / diag spec | 0x04 / 0x03 |
| DE00 | "Direct Configuration DID DE00" | **08** (one byte) |

Corroboration from the module's own EEPROM
(`PSCM/PSCM_EEPROM_DUMP_09042026.bin`, identical in the 2024 and 2026 dumps):
assembly `CV61-3C579-AL`, configuration ID `CV6C-3D070-LF`, VIN matches.

Earlier read (`ucds_/30052024_...uuw`) shows the same hardware running the
older `CV6T-14C217-AH` / `CV6T-14C218-AJ` / `CV6T-14C386-AA` set — i.e. this
same physical module has already accepted more than one software set.

---

## B. What configuration surface the PSCM actually exposes

### B.1 There is no 730 As-Built block on this vehicle family

Surveyed every As-Built export under `/home/gl/Projects/ford/ab/` (24 vehicles:
C346 Focus Mk3/3.5, C520 Kuga Mk2/2.5, US Escape, MY15–MY19):

* 23 of 24 have a `<NODEID>730` identification record (part numbers only) and
  **zero** `<DATA LABEL="730-xx-xx">` configuration blocks.
* The single exception, `WF0RXXGCDRCE16290.ab.xml`, has 26 `730-xx-xx` blocks —
  but that is a different vehicle line (Transit/Courier-family VIN `WF0RXXGCDR…`)
  and therefore a different PSCM programme.
* For contrast, IPMA (`706`) blocks appear in several of the same files, and
  BCM/IPC blocks appear in nearly all of them.

**Conclusion: Ford does not ship As-Built configuration codes to the
BV6T/JV6T-14C262 PSCM on C346/C520.** The forum recipes that quote
`730-01-01` / `730-02-01` / `730-02-02` "lane assist enable" bits are all from
*North American trucks and CD-platform/C2-platform vehicles* (F-150, Maverick,
2020 Escape, Explorer) whose PSCMs do have As-Built blocks. Those addresses do
not exist on this module. (Sources: f150forum "2019+ Adding Lane Keeping";
mavericktruckclub LKS retrofit; fordescape.org "(How-to) Enable Lane Centering
Assist"; forum.forscan.org t=23179. All community-sourced, unverified here.)

### B.2 What the PSCM does expose: the UCDS "Direct Configuration" FDxx set

Three UCDS Direct-Configuration captures exist locally:

| file | vehicle | notes |
|---|---|---|
| `Direct_PSCM_130426_.xml` | this car, C520 MY17 | F169 = `CV6C-3D070-LF` |
| `PSCM/Direct_PSCM_151024_.xml` | this car, earlier | F169 = `CV6C-3D070-LF`, steering-centre DIDs zeroed |
| `PSCM/Direct_PSCM_141024_.xml` | same VIN, F169 = `BV6C-3D070-AG` | a *donor/alternate* config set |
| `IPMA/ucds share/Direct_PSCM_221024_.xml` | a C346 MY11.25 car | third sample |

The complete DID set UCDS reads/writes on this module is:

```
F169  24 B ASCII  configuration part id  (e.g. "CV6C-3D070-LF")   identification
F190  24 B ASCII  VIN (byte-swapped in the capture)               identification
FD01  1 B        00 / 01 across samples          unknown single-byte config
FD04  1 B        01 / 02 across samples          unknown single-byte config
FD07  1 B        01 in all samples               unknown
FD08  2 B        0015 / 0007 / 001C / 0000       steering-position related*
FD09  1 B        00 / 01                         unknown
FD0A  6 B        36EB3BDF01CE etc.               per-unit calibration constants*
FD10  2 B        FFBC / FF99 / FFCE / 0000       steering-position related*
FD11  1 B        00 / 01                         unknown
FD12  1 B        00 / 01  (always inverse of FD11 in samples)  unknown
FD13  4 B        01000101 / 01010001             4 one-byte flags
FD14  4 B        = FD08 ++ FD10 exactly          composite read-back*
FD15  1 B        01 in all samples               unknown
FD21  1 B        00 / 01 / empty                 unknown
```

\* FD08/FD10/FD14 are almost certainly the stored straight-ahead / centring
offsets, not a feature flag: FD14 is byte-for-byte the concatenation of FD08 and
FD10 in every sample, and the 151024 capture (taken during a service procedure)
has all three at zero. Ford SSM 47526 confirms this module family stores a
"steering straight ahead position". This is *inference*, not documented fact.

**No meaning has been established for FD01, FD04, FD07, FD09, FD11, FD12,
FD13, FD15, FD21, or PSCM DE00.** They are plausible feature-enable flags by
shape (one byte, small enum values, varying across cars), but nothing in the
local material, the UCDS database, or any source found on the web assigns a
lane-assist meaning to any of them. **No DID or byte meaning is invented here.**

### B.3 Firmware-side search for a DID table: negative

Scanned all three 14C217 images (CV6T-AR, CV6T-AH, BV6T-AF) plus both 14C218
calibrations for an FDxx / Fxxx DID dispatch table — aligned and unaligned,
strides 1/2/4/6, ascending runs, and dense-window clustering. Best result was
4 distinct DIDs inside any 600-byte window; a real table would show 10+.
`work/find_service_tables.py` finds the SBL's service table but no application
DID table. So the writable-vs-read-only split could not be established from the
binaries, and is not asserted.

Note the DID values are *not* necessarily stored as literals — on DSP56800E
they may be composed arithmetically or held as byte pairs in a structure the
current tooling cannot locate. A negative here is weak evidence.

---

## C. Part-number and hardware-compatibility evidence

### C.1 BV6T vs CV6T is a software-generation label, not the hardware

Decisive, from the As-Built corpus — the *hardware* part (F111) and the
*software* part (F188) carry independent prefixes:

| vehicle | F111 (hardware) | F188 (software) | F124 (cal) |
|---|---|---|---|
| this car (C520 MY17) | BV6T-14C262-**AA** | CV6T-14C217-AR | CV6T-14C218-AX |
| Z6FAXXESMAGT56599 (Focus) | BV6T-14C262-AB | CV6T-14C217-AR | CV6T-14C218-AX |
| 2015_sync3 Escape | BV6T-14C262-AB | CV6T-14C217-AL | CV6T-14C218-AP |
| 2016 Focus ×4 | BV6T-14C262-AB | CV6T-14C217-AN | CV6T-14C218-AT |
| WF0AXXWPMAKM13456 (MY19) | JV6T-14C262-AB | HV6T-14C217-AC | HV6T-14C218-AD |
| 1FMCU9GDXKUA32718 (MY19 Escape) | JV6T-14C262-AB | HV6T-14C217-AC | HV6T-14C218-AD |

So **BV6T-14C262 hardware runs CV6T-prefixed software as standard.** The
"BV6T" in `BV6T-14C217-AF` is the *software* family label of a 2011-era build
targeting that same BV6T-14C262 core. There is no evidence anywhere in the
corpus of a distinct "BV6T PSCM hardware" versus "CV6T PSCM hardware".

### C.2 VBF headers: BV6T-14C217-AF and CV6T-14C217-AR are interchangeable images

| field | BV6T-14C217-AF | CV6T-14C217-AR | HV6T-14C217-AA/AC |
|---|---|---|---|
| `sw_part_type` | EXE | EXE | EXE |
| `ecu_address` | 0x730 | 0x730 | 0x730 |
| `network` / frame | CAN_HS / standard | same | same |
| erase map | `0x00000000+0x9800`, `0x0001C000+0x64000`, `0x04008C00+0x7400` | **identical** | **identical** |
| blocks / bytes | 3 / 478 208 | 3 / 478 208 | 3 / 478 208 |
| file size | 479 408 | 479 408 | 479 408 |

Same target address, same erase regions, byte-identical payload geometry.
Likewise `14C218` (BV6T-AF and CV6T-AX both erase `0x9800+0x12800`, 75 776 B)
and `14C386` (both erase `0x04008000+0xC00`).

VBF 2.2/2.3 headers carry **no `sw_part_type` compatibility/dependency field
and no hardware-part gate** — there is nothing in the header that would prevent
a BV6T application from being written to this module. Acceptance would be
decided by the bootloader's own checks (and by whatever the FDRS/UCDS driver
file allows), which is not visible from the VBF.

### C.3 What the documentation says about which cars had LCA

* Ford's own UK support page describes Lane Centering as part of **Adaptive
  Cruise Control with Stop-and-Go**, with its own steering-wheel button —
  i.e. a *system-level* feature, not a PSCM option.
  (ford.co.uk, "What is Ford Adaptive Cruise Control with Stop-and-Go and Lane
  Centering?")
* Ford Kuga Mk2 (2013–2018, C520 — **this car**) owner documentation lists only
  **Lane Keeping Alert / Lane Keeping Aid**; Lane-Centring first appears in the
  **2020 Kuga (Mk3 / CX482)** launch material alongside Co-Pilot360.
  (mycarusermanual.com Kuga 2013-2018 vs Kuga 2020; autodevot 2020 Kuga launch.)
* Community consensus (hearsay, but consistent across sources) on Focus Mk4 /
  Fiesta Mk8 is that LCA is tied to the automatic-transmission ACC-Stop&Go
  package and is enabled in IPMA/IPC/SCCM/PSCM configuration — and that on
  platforms where it *is* config-gated, the gate is visible as named PSCM
  settings ("lance centering assist", "traffic jam assist") in FORScan's Easy
  mode. **No such named PSCM setting has ever been reported for C346/C520.**
  (forum.forscan.org t=11371; fordescape.org 117546.)

So on the C520/C346 PSCM programme, LCA was never a shipped feature at all —
which is consistent with there being no As-Built block and no named setting.

---

## D. Verdict

**A configuration route to enabling LCA on this PSCM is unlikely but not
strictly disproven.**

Against it (verified):
1. No 730 As-Built configuration blocks exist on any C346/C520 vehicle in the
   local corpus — the whole FORScan As-Built mechanism is absent for this module.
2. The only configuration surface is the UCDS FDxx set: ~13 DIDs, mostly
   single bytes, dominated by steering-centre calibration and identification.
3. No named lane/centering setting is reported by any tool for this module.
4. The feature was never offered on this platform, so a dormant enable flag has
   no product reason to exist.

For it (weak):
1. The FDxx set contains 8–10 undocumented one-byte flags whose meanings are
   genuinely unknown, and they *do* vary between vehicles. One of them could be
   a lane-assist capability/level enum.
2. PSCM `DE00 = 08` is a single undocumented config byte we have never varied
   or compared against another C520.
3. The prior IPMA finding (DE00–DE03 are pure feature-enable enums) shows Ford
   does encode capability enums in exactly this kind of DID.

Crucially, the parallel firmware work already found that CV6T is a strict
*superset* of BV6T (+52 structures, +185 functions) and handles the LKA/LCA
enum identically. That argues the difference — if any — is not a removed
feature. A configuration flag is therefore the most plausible remaining
non-firmware explanation, which is precisely why the FDxx bytes are worth
characterising before anyone contemplates flashing.

---

## E. Recommendation — cheapest decisive experiments, in order

All are **read-only** except step 4, which is reversible and must be preceded by
a full FDxx backup.

1. **Read the whole FDxx space.** `22 FD00` … `22 FD2F`, plus `22 DE00`…`22 DE07`,
   in default and extended session. Record NRCs. This establishes which DIDs
   exist beyond the 13 UCDS reads, which we have never enumerated directly.
2. **Probe writability without writing.** `2E <DID>` with the *current* value
   read back from `22`. A `0x31` (request out of range) / `0x7F 2E 22` tells us
   read-only; a positive response is a no-op rewrite. Do this only for the
   single-byte flags, never for FD08/FD10/FD14/FD0A (steering centre).
3. **Obtain a second C520/C346 PSCM FDxx dump** — ideally from a car with a
   different feature level. The single most informative datapoint available.
   We currently have three samples; a fourth with known-different lane options
   would isolate any candidate byte immediately.
4. **Only if a candidate byte is isolated by 1–3**, toggle it with the
   original value recorded, engine off, battery supported, and verify by
   re-reading. Do not toggle FD08/FD10/FD14/FD0A under any circumstances —
   those are the straight-ahead/centring calibration and corrupting them is a
   real safety defect (SSM 47526 documents the symptom).

Do **not** attempt a BV6T-14C217-AF cross-flash on the strength of the
header-compatibility finding alone. The headers show it is *mechanically*
possible (same ecu_address, same erase map, same hardware core family) — they do
not show it is *correct*. The 14C218 calibration is not alignable between the
two families (1.9 % positional overlap, see `PSCM_14C218_calibration_compare.md`),
so a BV6T application would be running against a CV6T calibration or would
require flashing the BV6T calibration too, on a module whose EEPROM-stored
per-unit constants were written for the CV6T set.

---

## F. Confidentiality note

The vehicle CAN database under `/home/gl/Projects/ford/CANBus/` was consulted
only to confirm that the lane-assist request state and the steering-status
response are single-frame signals on the main high-speed bus; no signal or
frame identifiers from it are reproduced here.
