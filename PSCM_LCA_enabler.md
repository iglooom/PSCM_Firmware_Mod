# Ford PSCM `CV6T-14C217-AR` — Lane Centering Aid enabler

Reference for enabling working LCA (Lane Centering Aid) steering torque on a
Ford PSCM (electric power steering module) running `CV6T-14C217-AR`.

> Also ported to the later `HV6T-14C217-AC` (2018) revision — same fix, same
> checksum algorithms, relocated offsets. See §6a.

Stock behaviour: LCA displays on the dash, the PSCM accepts the camera's
command and reports itself available, but applies no usable steering torque.
LKA (Lane Keeping Aid) works normally.

Five word-sized edits fix it. No calibration constant is changed; every edit
redirects execution to an OEM code path the module already runs.

**Verified on the vehicle:** LCA steers, with peak torque authority comparable
to LKA (measured ratio 1.19).

> Right-to-repair modification of your own vehicle. This is a safety-critical
> steering module — read §7 before flashing.

---

## 1. Target

| | |
|---|---|
| Module | PSCM, electric power steering |
| MCU | Freescale MC56F8366, DSP56800E, 16-bit word-addressed |
| Diagnostic IDs | tx `0x730` / rx `0x738` |
| Application | `CV6T-14C217-AR` (`14C217`) |
| Paired calibration | `CV6T-14C218-AX` (`14C218`) |
| Signal config | `CV6T-14C386-AB` (`14C386`) |

```text
CV6T-14C217-AR.VBF  sha256 cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5
CV6T-14C218-AX.VBF  sha256 6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7
```

Supported revisions (identical topology, MCU, diagnostic IDs and checksum
algorithms; §6a covers the port and per-revision offsets):

| Application | Paired calibration | Status |
|---|---|---|
| `CV6T-14C217-AR` | `CV6T-14C218-AX` | flashed and driven |
| `HV6T-14C217-AC` (2018) | `HV6T-14C218-AD` | built + verified, not yet driven |

### P-space memory map

`14C217` ships as three blocks; `14C218` fills the gap between blk0 and blk1.

| Region | P-space words | Content |
|---|---|---|
| `14C217` blk0 @ `0x00000000` | `P:$00000–$04BFF` | code |
| `14C218` blk0 @ `0x00009800` | `P:$04C00–$0DFFF` | calibration |
| `14C217` blk1 @ `0x0001C000` | `P:$0E000–$3FFFF` | code (all patch sites) |
| `14C217` blk2 @ `0x04008C00` | `X:$4600+` | data flash |

**Converting a blk1 P-address to a file offset** (all patch offsets below use
this):

```text
byte_offset_in_blk1 = (P_word_address - 0x0E000) * 2
```

---

## 2. How lane assist works inside this firmware

### 2.1 Request path

The IPMA (camera) sends CAN `0x0A5`. Its lane-request enum arrives at the
lane-state dispatcher, which converts it to an **internal lane state**
`X:$2DDE`:

```text
CAN request 2  ->  X:$2DDE = 1     LKA left
CAN request 4  ->  X:$2DDE = 2     LKA right
CAN request 6  ->  X:$2DDE = 4     LCA
```

A **consumer state machine** then turns the lane state into a **per-state
code** `X:$2DB9`, which is the value the whole torque chain switches on:

| `X:$2DB9` | Meaning |
|---|---|
| 0, 3 | idle |
| 1 | LKA sustained |
| 2 | LKA transient |
| 4 | LCA entry |
| **5** | **LCA sustained** |

> These are the *live* codes, established by runtime measurement. Sustained LKA
> is code **1**, not 2. Any static analysis that compares code 2 against code 5
> is comparing two codes that share the same arms and will wrongly conclude
> "LKA and LCA are treated identically".

### 2.2 Torque chain

```text
         dispatcher 1 (P:$2AFA4)          dispatcher 2 (P:$2B025)
         per-state RAMP INCREMENT          per-state AUTHORITY INCREMENT
                  |                                  |
                  v                                  v
   rate limiter P:$2AFEA                   torque fn P:$2B07C
   ramp X:$2D47 += increment,              integrator X:$2D4B += X:$2D4A
   clamped to +-X:$2D46                    saturated at 0x7FFE
                  |                                  |
                  +---------> demand <---------------+
                              X:$2D49 = (X:$2D54 * X:$2D47) >> 10
                                          |
                                          v
                            torque accumulator X:$2D53
```

Both dispatchers are 6-entry jump tables, stride 2 words, indexed by `X:$2DB9`.

### 2.3 Key RAM cells

| Cell | Meaning |
|---|---|
| `X:$2DDE` | internal lane state |
| `X:$2DB9` | per-state code (selects the dispatcher arm) |
| `X:$2DC1` | consumer entry gate — **non-zero inhibits** phase-1 entry |
| `X:$2D47` | ramp value (rate-limiter output) |
| `X:$2D46` | ramp clamp |
| `X:$2D54` | control-law output |
| `X:$2D49` | demand = `(X:$2D54 * X:$2D47) >> 10` |
| `X:$2D4A` | authority increment (added to the integrator each cycle) |
| `X:$2D4B` | integrator |
| `X:$2D53` | torque accumulator (the output) |
| `X:$0904` | read-only calibration; the LCA entry gate value |

---

## 3. The five defects

All five have the same shape: for LCA, execution reaches a degenerate path
while LKA (code 1) and LCA-entry (code 4) reach the working one.

| # | Site | Defect |
|---|---|---|
| 1 | `P:$2A780` | dispatcher gate — a 4-word guard blocks `X:$2DDE = 4` (LCA state entry) |
| 2 | `P:$2ABFB` | phase-1 entry — `X:$2DC1 != 0` skips the phase/code entry writes |
| 3 | `P:$2B8B0` | availability producer — code 5 forces transmitted availability to 1, so the IPMA withdraws its request within ~60 ms |
| 4 | `P:$2AFAE` | dispatcher-1 code-5 entry points at an arm that sets the ramp increment to **literal 0** — the ramp can never grow, so demand stays ~0 |
| 5 | `P:$2B02F` | dispatcher-2 code-5 entry points at an arm giving authority increment **−9** — a decay term that bleeds the integrator to zero |

Defects 4 and 5 are the ones that actually starve the torque. 1–3 must be
fixed first or LCA never reaches the torque chain at all.

### Why defect 4 is easy to miss

The rate limiter takes **two** inputs: the clamp `X:$2D46` and the per-cycle
increment (register `Y0`). The clamp is *identical* for LKA and LCA — only the
increment differs. Instrumenting the clamp shows no difference and leads to the
wrong conclusion.

```text
code 1  arm P:$2AFB0   Y0 = divider result    computed
code 4  arm P:$2AFD8   Y0 = divider result    computed
code 5  arm P:$2AFE2   Y0 = 0    (E580)       <- defect
```

---

## 4. The patch

All offsets are **bytes into `14C217` blk1** (the block loading at flash
`0x0001C000`). Words are little-endian. Assert every stock value before
writing — a mismatch means the wrong image or a shifted layout.

| # | Offset | Stock | Patched | Effect |
|---|---|---|---|---|
| 1 | `0x38F00` | `F07C 0904 4C01 A203` | `E700 E700 E700 E700` | NOP the LCA entry gate |
| 2 | `0x397F6` | `FF7C 2DC1 A209` | `E700 E700 E700` | NOP the phase-1 inhibit test |
| 3 | `0x3B160` | `A303` | `E700` | NOP the code-5 availability branch |
| 4 | `0x39F5C` | `AFE2` | `AFD8` | code 5 uses the computed-ramp arm |
| 5 | `0x3A05E` | `B04E` | `B031` | code 5 uses the +40 authority arm |

Patches 4 and 5 are single **table entries**, not code: they repoint code 5 at
the arm code 4 already uses. Both arms are left untouched, and codes 0/2/3
continue to use the original arms.

### Context

Patch 1 — the `==6` arm of the lane-state dispatcher at `P:$2A77E`:

```text
4C06 A207  F07C 0904 4C01 A203  E684 2DDE  A902 E680 2DDE
CMP #6     <--- the 4-word guard --->      MOVE #4,X:$2DDE
```

Patch 2 — phase-1 LCA entry at `P:$2ABF7`:

```text
F07C 2DDE 4C04 A20C   FF7C 2DC1 A209   E700 4C83 A206
test state == 4       <-- inhibit -->
```

Patch 3 — availability producer at `P:$2B8A8`. Only the code-5 test is
neutralised; the code-2 and code-3 predicates must survive:

```text
F07C 2DB9 4C02 A306 E700 4C05 A303 E700 4C03 A203
                         ^^^^ ^^^^
                         code-5 branch -> E700
```

Patch 4 and 5 — the dispatcher tables (entry 5 is the last, at table + 10
words):

```text
d1 P:$2AFA4:  AFE7 AFB0 AFCF AFD4 AFD8 [AFE2 -> AFD8]
d2 P:$2B025:  B04F B031 B04E B04F B031 [B04E -> B031]
              c0   c1   c2   c3   c4    c5
```

### Measured result

| | Stock | Patched |
|---|---:|---:|
| peak \|accumulator\| LCA | 28 | 2000 |
| peak \|accumulator\| LKA | 1923 | 1676 |
| longest sustained LCA episode | 0.7 s | 9.4 s |

---

## 5. Integrity repair

`14C217` blk1 carries **two internal checksums** that the module verifies.
Both must be recomputed, in this order, after patching and before the
container CRCs.

### Word A — `0x63FEA` in blk1 (stock `D110`)

CRC-16/MCRF4XX (poly `0x8408` reflected, init `0xFFFF`) over a **gapped
concatenation**: all of blk0, followed by blk1 from `START` up to but not
including the word-A offset.

```python
def crc16_mcrf4xx(data, init=0xFFFF):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

word_a = crc16_mcrf4xx(blk0 + blk1[START:0x63FEA])
```

`START` is `0x1800` for this build. It is discoverable rather than hardcoded:
locate the 32-bit immediate near the `FFF5 0003` end-marker whose value lands
in `0x0000E000..0x0000F000`, then `START = value * 2 - 0x1C000`.

### Word B — `0x63FEC` in blk1 (stock `3216`)

16-bit little-endian sum over a linear image of the **whole module** —
`14C217` blk0 + patched blk1 + blk2, **plus the paired `14C218` calibration** —
laid into a `0xFF`-filled buffer at each block's flash address, truncated at
`0x0007FFEC`.

```python
def sum16le(data):
    return sum(struct.unpack_from("<H", data, o)[0]
               for o in range(0, len(data) - 1, 2)) & 0xFFFF
```

Sanity check: the stock `14C218` block sums to `0xFFFF` on its own.

### Container layer

After the internal words, repair the VBF itself:

1. per-block **CRC-16/CCITT-FALSE** (`binascii.crc_hqx(data, 0xFFFF)`), stored
   big-endian immediately after each block's payload;
2. header `file_checksum` = **CRC-32** of everything from the end of the header
   brace to EOF, written back as uppercase hex in place.

The VBF header contains nested braces — find the closing brace by depth
counting, not by the first `}`.

---

## 6. Flashing

### Prebuilt image

```text
CV6T-14C217-AR_LCA_ENABLED.VBF
sha256 24a9b235920e0eb54eb233445577630d2846574efb556a9aad74c5e207f49999
```

Five control patches, no telemetry, both internal checksums and all container
CRCs valid. Exactly **22 bytes** differ from stock: nine patch words (two of
which differ in their low byte only) plus the two checksum words. Blocks 0 and
2 are byte-identical to stock.

Build and verify it yourself with:

```bash
python3 work/lca_resume/build_lca_final_vbf.py --selftest
python3 work/lca_resume/build_lca_final_vbf.py
python3 work/lca_resume/verify_lca_final_vbf_independent.py
```

The verifier is a deliberately separate implementation — its own container
reader and its own restated constants — and recomputes both internal checksums
from scratch rather than trusting the builder.

### Procedure

The module is flashed over UDS with the standard Ford SBL
(`BV6T-14C220-AA.vbf`, loaded to RAM at `0x0004F800`). Any flasher that
handles Ford VBF containers for `0x730` will do.

```bash
# verify container integrity first
vbflasher.py verify CV6T-14C217-AR_LCA_ENABLED.VBF
# dry run resolves target + SBL without touching the module
vbflasher.py flash --dry-run CV6T-14C217-AR_LCA_ENABLED.VBF
vbflasher.py flash CV6T-14C217-AR_LCA_ENABLED.VBF
# then power-cycle the ignition
vbflasher.py dtc PSCM
```

Reverting is a normal flash of the stock `CV6T-14C217-AR.VBF`.

---

## 6a. Porting to another revision — HV6T-14C217-AC

The same enabler applies to the later `HV6T-14C217-AC` (2018) revision. **The
checksum layer is fully compatible** — no algorithm change was needed:

| Layer | Reproduces stock on HV6T-AC? |
|---|---|
| word A (CRC-16/MCRF4XX, gapped, `START`=`0x1800`) | yes → stored `B022` |
| word B (`sum16le` whole-module incl. `14C218`, end `0x7FFEC`) | yes → stored `D35E` |
| paired-cal self-sum sanity (`14C218-AD` = `0xFFFF`) | yes |
| container CRC-16/CCITT + file CRC-32 | yes |

What **did** change is the code layout: HV6T-AC is an isomorphic revision — the
lane-assist code is instruction-for-instruction identical, but flash addresses
shifted and the RAM cells moved by a fixed delta (`X:$2DDE→2E38`,
`X:$2DC1→2E1B`, `X:$2DB9→2E13`, torque cells `2D46→2DA0` etc., cal gate
`0904→090A`; the `X:$03B0` tuning knob is unchanged). Every patch site was
relocated structurally (opcode-pattern + cell-wildcard search) and its stock
words re-asserted:

| # | HV6T-AC blk1 off | Stock | Patched | (CV6T-AR off) |
|---|---|---|---|---|
| 1 | `0x39144` | `F07C 090A 4C01 A203` | `E700 E700 E700 E700` | `0x38F00` |
| 2 | `0x39A3A` | `FF7C 2E1B A209` | `E700 E700 E700` | `0x397F6` |
| 3 | `0x3B3A4` | `A303` | `E700` | `0x3B160` |
| 4 | `0x3A1A0` | `B104` | `B0FA` | `0x39F5C` |
| 5 | `0x3A2A2` | `B170` | `B153` | `0x3A05E` |

Patches 4/5 are dispatcher entry-5 repoints exactly as on CV6T: d1 code-5→code-4
arm (`B104→B0FA`), d2 code-5→code-1/build arm (`B170→B153`); entries 0–4 and the
stride words are frozen in both tables.

```text
HV6T-14C217-AC.VBF            sha256 0915dfde81f2f76d8740e8a1b0cf6497b65b521be431f04843c2380c0f0f9bc6
HV6T-14C218-AD.VBF (paired)   sha256 fff77d9eaf25da53df9b42381cb5cbba1d9801b770ba279548c340288c3a4179
HV6T-14C217-AC_LCA_ENABLED.VBF sha256 fb2003871bd8fffb0534e2bf6cbd97b63a38d6d7980a9d3134f3f3554d45c6df
```

The output differs from stock in exactly **23 bytes** (nine patch words — two
differing in the low byte only — plus the two internal checksum words); blocks 0
and 2 are byte-identical. Build and independently verify:

```bash
python3 work/lca_resume/build_lca_hv6t_vbf.py --selftest
python3 work/lca_resume/build_lca_hv6t_vbf.py
python3 work/lca_resume/verify_lca_hv6t_independent.py   # separate impl, recomputes A+B
```

> Not yet flashed to a vehicle. The control-path logic is proven on CV6T-AR;
> the HV6T-AC relocation is structurally verified but the on-car drive test
> (§4 measured result) has not been repeated on this revision.

---

## 7. Safety notes

- **Keep the stock VBF.** It is the rollback path and reflashes cleanly.
- **Assert every stock value before writing.** A layout mismatch that is
  silently patched produces arbitrary behaviour in a steering module.
- **Verify with a second, independent implementation** before flashing:
  re-parse the built container with separate code and re-derive every claim.
  Confirm codes 0–4 dispatch is unchanged in both tables and that no arm,
  the rate limiter, the demand stage, or the torque function was modified.
- **First drive:** empty straight road, both hands on the wheel, brief
  engagement, ready to override. Abort on oscillation, on torque that fights
  an override, or on any steering DTC.
- The torque function saturates at `0x7FFE`; measured peak after patching is
  ~2520, so there is large headroom. Authority is bounded by the ramp clamp,
  not by saturation.

### Tuning

If LCA feels too strong, do **not** revert patch 4 — that restores a zero ramp.
The knob is `X:$03B0`, the divider numerator inside the code-4 arm:

```text
P:$2AFD8   8745 0400      C1 = 0x400      denominator
           F77C 03B0      Y1 = X:$03B0    numerator   <- the knob
           E254 011F      JSR divider
```

`X:$03B0` is read-only calibration in the `14C218` block and is shared with
code 4 (LCA entry) only — **not** with LKA — so lowering it affects LCA alone.

---

## 8. Known limitation: the ~3.7 s intervention cap

Lane assist stops steering after about 3.7 seconds and re-arms. **This is the
IPMA, not the PSCM, and it cannot be fixed in PSCM firmware.**

Measured across five drives (69 LKA episodes): 77 % of episodes end at
3.70–3.72 s with near-zero spread within a drive; the PSCM still reports
available with no deny at the moment the request drops; and the request always
becomes "suppressed left+right" rather than "idle", meaning the camera still
tracks the lane and suppresses itself.

This is the expected regulatory pattern for hands-on lane keeping. The PSCM is
the compliant party — no change to `14C217` can extend an intervention the
camera stops requesting.

---

## 9. Optional: runtime telemetry

The application's UDS service layer can be extended to read arbitrary RAM over
diagnostics, which is how the values above were measured. A 60-word code cave
at `P:$33800` (stock: `E70A` filler) plus a callback-pointer redirect at
`P:$0EEA4` (stock `058F 0001`) yields a snapshot DID returning seven 16-bit
cells per request.

Handler shape, 60 words, one format nibble plus 7 sources:

```text
E08<fmt> D0B6                          prologue, format id
per source:  F07C <cell> 8110 5C28 D0E6 <lo> D1E6 <hi>
E58F E708                              epilogue, RTS
```

This is telemetry only and changes no control path. It is not required for the
fix, and can be omitted entirely.
