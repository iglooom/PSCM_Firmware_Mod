# PSCM CAN ID handling — where 0xA5 lives

Target: `CV6T-14C217-AR` (main) + `CV6T-14C218-AX` (data) + **`CV6T-14C386-AB`
(signal configuration)**, Freescale MC56F8366, DSP56800E, 16-bit word-addressed.

## 1. Answer: 0xA5 IS handled — via the 14C386 signal-configuration block

`0xA5` (165) = `IPMA_h_FrP01`, the IPMA→PSCM lane-keeping frame. It is
configured as a **received** message in a CAN mailbox descriptor table inside
the `14C386` *Signal Configuration* VBF:

```
record  X:$421A   ID 0x0A5   RX   IPMA_h_FrP01
        payload buffer  X:$3F6A  (4 words = 8 bytes)
        message state   X:$7F4E  (2 words: rolling count / timeout)
        signal mask     X:$42BC
        slot bit        0x0020        flags 0x0048 (normal application frame)
```

> **Correction.** An earlier pass concluded 0xA5 was unhandled. That was wrong:
> it scanned only `14C217` (+`14C218`) and looked for the ID as an *instruction
> operand*. `14C386` is a **third, separate VBF** and the ID lives in a data
> table, not in code — invisible to both checks. The prior negative result is
> retracted.

### Verified against the live unit

`14C386` loads at byte `0x04008000` → `X:$4000`, the start of data flash, just
**below** the `14C217` blk2 calibration at `0x04008C00` (`X:$4600`):

| Region | Live-dump check |
|---|---|
| `14C386-AB` signal config, `0x04008000 +0x79C` | **byte-exact match** |
| gap `+0x79C .. +0xC00` | erased `0xFF` |
| `14C217-AR` blk2, `0x04008C00 +0x7400` | **byte-exact match** |

(`dumps/PSCM_dflash_04008000_20260913T173221Z.bin`, 32 KB, read from the car.)
So the table above is what the module is actually running, not just what the
OEM file contains.

## 2. Descriptor format (`work/disasm/signal_config.py`)

20 records of **10 words**, in two groups, each with a pointer table:

| Word | Meaning |
|---|---|
| +0 | CAN identifier, **raw 11-bit** (not the FlexCAN `ID<<2` layout) |
| +2 | X-RAM pointer to the 8-byte frame payload buffer (stride 4) |
| +3 | index within group |
| +4 | X-RAM pointer to per-message state (stride 2); `0` for TX |
| +5 | pointer into the signal-mask area at `X:$42B0`; `0` for diagnostic |
| +8 | one-hot slot bit |
| +9 | `0x0048` normal application frame / `0x0058` diagnostic |

Pointer-table entries are `{record_addr, 0x0000, direction, 0x0001}` with
direction `0x0005` = **TX**, `0x0007` = **RX**. Table addresses differ between
builds, so the tool locates them **by shape** and derives the record count by
walking until the shape breaks — not hardcoded.

**Independent confirmation the fields are right:** direction `0x0005` marks
exactly the four frames a PSCM should transmit — `0x0B0 PSCM_h_FrP00`,
`0x140 PSCM_h_FrP01`, `0x695 CCP_PSCM_FrTx`, `0x738 TST_PhysicalRespPSCM` —
and every one of the 20 IDs resolves to a real CAN-HS DBC name. A wrong field
offset would not produce 20/20 valid IDs with correct TX/RX polarity.

### Full table (CV6T-14C386-AB)

TX: `0x0B0`, `0x140`, `0x695`, `0x738`
RX: `0x080`, **`0x0A5`**, `0x170`, `0x420`, `0x696`, `0x730`, `0x7DF`,
`0x010`, `0x0C8`, `0x160`, `0x180`, `0x190`, `0x1C0`, `0x1E0`, `0x400`, `0x405`

### Build delta vs `BV6T-14C386-AA` (2011)

* `+ 0x0B0` PSCM_h_FrP00 (new TX)
* `+ 0x420` BCM_h_FrP10 (new RX)
* `− 0x090` ECM_h_FrP03 (dropped)

`0xA5` is present in **both** builds — this PSCM has consumed the IPMA
lane-keeping frame since 2011.

## 3. How the firmware reaches it

The descriptor addresses appear **nowhere** as instruction operands in
`14C217`: the code walks the pointer tables generically, so mailbox setup is
fully data-driven. Consistent with this:

* All four FlexCAN vectors (82–85) are `E70A` filler — the module **polls**.
* Only `0x730`/`0x736` diagnostics use absolutely-addressed mailboxes
  (MB12/MB13, `X:$F8A0..$F8AD`); application traffic never does.

Practical consequence: **CAN message routing is changed by reflashing 14C386,
not 14C217.** It is only ~1.9 KB and, unlike `14C217` blk0/blk1, is not covered
by the blk1 word-A CRC (`PSCM_internal_checksums.md`).

## 4. Which signals are used vs ignored (`work/disasm/signals_used.py`)

Each descriptor's +5 field points at a 4-word (64-bit) mask in the table at
`X:$42B0`. **Every bit it clears is an `_UB` (Update Bit) signal — 30 of 30**,
across all 13 masked messages. On this platform each subscribed signal carries
a companion `<signal>_UB` update bit, so the set of tracked update bits is the
set of consumed signals.

Two things are established from the data rather than assumed:

* **Bit order.** Scoring cleared bits against DBC signal boundaries:
  lsb-first = **30 whole signals / 0 partially cut**; msb-first = 13 whole /
  14 cut. The right convention snaps to boundaries, the wrong one smears.
* **Meaning.** 30/30 landing on the `_UB` suffix is not chance, and every
  resulting set is exactly what a steering module needs — and nothing it
  doesn't.

### 0xA5 `IPMA_h_FrP01` — the lane-keeping frame

The mask tracks update bits, so it speaks only about signals that *have* a
`_UB` companion. On 0xA5 only two do:

| | Signals |
|---|---|
| **SUBSCRIBED (2)** | `LaCurvature_No_Calc`, `LaRefAng_No_Req` |
| **UNDETERMINED (7)** — no update bit, mask is silent | `LaActvReq_No_RollCnt`, `LaActvStats_No_Cs`, `LaRampType_B_Req`, `LaStePar_No_Cs`, `LdwActvIntns_D_Req`, `LdwActvStats_D_Req`, `LkaActvStats_D_Req` |

The PSCM definitely consumes the two **physical steering commands** — road
curvature and reference steering angle. The remaining seven cannot be called
"ignored" from this table alone: a signal with no update bit is simply outside
what the mask encodes. Establishing their status needs the 14C217 code path
that reads the payload buffer at `X:$3F6A`.

> **Earlier error, corrected.** A previous version of this document listed
> those seven as "IGNORED". That over-claimed: it treated "has no update bit"
> as "is not used". `signals_used.py` now reports three categories —
> SUBSCRIBED / UB-NOT-SET / UNDETERMINED.

### All messages

| ID | Dir | Used | Ignored |
|---|---|---|---|
| `0x010` SASM | RX | `SteeringAngle` | 7 (CR/CS/counter/sign/status) |
| `0x080` ECM | RX | `GearRvrseActv_D_Actl`, `PrplWhlTot_Tq_Actl` | 6 |
| **`0x0A5` IPMA** | RX | `LaCurvature_No_Calc`, `LaRefAng_No_Req` | 7 |
| `0x0B0` PSCM | TX | `EPSSteeringAngle`, `EPSSteeringAngleLimit`, `EPSSteeringAngleStatus`, `EpsDrvInfo_D_Dsply`, `KeepNetworkPSCM`, `SAPPAngleContrError` | 1 |
| `0x0C8` BCM | RX | `CarMode`, `PowerMode`, `PwMdeExten_D_Actl` | 24 |
| `0x140` PSCM | TX | `EngRun_D_ReqSte`, `TorsionBarTorque` | 7 |
| `0x160` ABS | RX | `AbsActv_B_Actl`, `StabCtlBrkActv_B_Actl`, `TCMode`, `TracCtlPtActv_B_Actl` | 7 |
| `0x170` PAM | RX | `ExtSteeringAngleReq` | 10 |
| `0x180` ABS | RX | `VehLatComp_A_Actl`, `VehYawComp_W_Actl`, `YawStabilityIndex` | 4 |
| `0x190` ABS | RX | all 4 wheel speeds `WhlFl/Fr/Rl/Rr_W_Meas` | 0 |
| `0x1C0` ABS | RX | `RSCMode` | 12 |
| `0x1E0` ABS | RX | `Veh_V_ActlBrk` | 8 |
| `0x420` BCM | RX | – | `ManRgen_D_Rq` |

Seven messages carry **no** mask (whole-frame handling): the diagnostic and
CCP frames `0x730`, `0x738`, `0x7DF`, `0x695`, `0x696`, plus `0x400`/`0x405`
BCM vehicle-configuration.

### Open: the second table at `X:$42E4`

A separate 6-word-per-record table (keyed by payload-buffer pointer, only 7
messages) also holds 64-bit masks. Its polarity is **genuinely ambiguous** —
neither reading is semantically coherent across all seven — so it is
deliberately left undecoded rather than guessed. Likely a per-signal timeout
or default-value selector. `work/disasm/signal_usage.py` dumps it raw.

## 5. Disassembler (`work/disasm/flow56800e.py`)

Recursive-descent on the 569-encoding ERM table, with two fixes:

1. **Conditional branches were missing entirely** — `extract_encodings.py`
   filters mnemonics with `^[A-Z][A-Z0-9_.]*$`, which rejects the lowercase
   "cc" in `Bcc`/`Jcc`. Added from ERM A-57/A-153 + Table A-18.
2. **`CCCC` bit order**: the split field's standalone bit 2 is the **MSB**.
   Verified — that reading puts the six unconditional encodings exactly on
   Table A-18's three *Reserved* codes (`1001` BRA/JMP, `1010` BSR/JSR,
   `1011` BRAD/JMPD) across both grids; the opposite reading collides `JMP`
   with `Jeq`, `JSR` with `Jlt`, `JMPD` with `Jle`.

20 self-test checks pass; `dis56800e.py` and `wordA_solved.py` unaffected.

## 6. Lessons

* **Enumerate the whole bundle first.** A three-part firmware means three
  address ranges; "not found" is meaningless until every part is loaded.
  Here the answer sat in the smallest file (1.9 KB of 590 KB).
* **Config data is not code.** Searching for an ID as an instruction operand
  cannot find a table-driven subscription. Match the *data shape* instead.
* **A bare literal match is not a cross-reference** — but neither is its
  absence a proof. `0x00A5` at `X:$421A` was in the very first byte-level
  scan output and was dismissed as calibration noise; the record structure
  around it was what made it meaningful.

## 7. Tools

```bash
python3 work/disasm/signal_config.py        # decode the CAN mailbox table
python3 work/disasm/flow56800e.py --selftest
python3 work/disasm/flow56800e.py --func 0x19DD5
python3 work/disasm/can_registrations.py    # 113 IDs referenced from 14C217 code
```
