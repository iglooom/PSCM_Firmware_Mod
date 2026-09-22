# PSCM — disable the IPC hands-off warning (force "hands on")

**Test/research modification for your own vehicle.** Makes the PSCM always
transmit `LaHandsOff_B_Actl = 0` ("Hands on") in CAN frame `0x140`, so the IPMA
camera never escalates the hands-off warning shown in the IPC cluster. Lane
assist keeps steering exactly as stock; only the *reported* hands-on/off status
is pinned.

> Safety-critical steering module. Keep the stock VBF — it is the rollback path
> and reflashes cleanly. This changes only the status the PSCM broadcasts, not
> the torque it applies.

---

## 1. Target

| | |
|---|---|
| Module | PSCM, electric power steering |
| MCU | Freescale MC56F8366, DSP56800E, 16-bit word-addressed |
| Diagnostic IDs | tx `0x730` / rx `0x738` |
| Application | `CV6T-14C217-AR` (`14C217`) |
| Paired calibration | `CV6T-14C218-AX` (`14C218`) |

```text
CV6T-14C217-AR.VBF  sha256 cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5
CV6T-14C218-AX.VBF  sha256 6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7
```

Blk1 loads at flash `0x0001C000` (`P:$0E000`). Convert a blk1 P-address to a
file offset with `byte_offset = (P_word - 0x0E000) * 2`.

---

## 2. What the signal is and where it comes from

`LaHandsOff_B_Actl` is CAN-HS frame **`0x140` `PSCM_h_FrP01`, bit 61 = byte 7
bit 5** (`1` = Hands off, `0` = Hands on). It is protected inside the same
frame by `LaActStats_No_Cs` (checksum, byte 6) and `LaActStats_No_RollCnt`
(rolling counter). The IPMA reads it and drives `LaHandsOff_D_Dsply` (frame
`0x265`, BCM→IPC) to escalate the cluster warning (Visual → Audible → LCA
suppressed).

On the wire (running capture, `hscan_running_still.log`, 2784 frames): byte 7 is
`0x24` when hands-off, `0x04` when hands-on — bit 5 (`0x20`) is the signal;
bit 2 is `LaActAvail` (constant 1); byte 6 varies as the checksum.

### Firmware data path

The status-frame **composer** at `P:$1DB5D` reads three producer cells and
packs them into frame `0x140` byte 7:

```text
X:$2252  LaActAvail_D_Actl  bits 3:2   (<- X:$2D28)
X:$2251  LaActDeny_B_Actl   bit 4      (<- X:$2DB8)
X:$2250  LaHandsOff_B_Actl  bit 5      (<- X:$2DAA)
```

The three sources are copied into place immediately before composing, at
`P:$2B8DA..P:$2B8E0`:

```text
P:$2B8DA  F67C 2D28 2252   X:$2D28 -> X:$2252   availability
P:$2B8DD  F67C 2DB8 2251   X:$2DB8 -> X:$2251   deny
P:$2B8E0  F67C 2DAA 2250   X:$2DAA -> X:$2250   hands-off   <-- patch here
P:$2B8E3  E708             RTS
```

`X:$2DAA` is the output of the hands-off **detection** state machine at
`P:$2AD8C` (torsion-bar magnitude vs. calibration thresholds `X:$0357`,
`X:$0358`, `X:$0359`, with a debounce counter `X:$2DA9`). `X:$2250` is written
**only** by the copy at `P:$2B8E0` and read **only** by the composer, so
neutralising that one copy pins the *transmitted* bit without disturbing
detection, deny, availability, or the checksum/rolling-counter (both recomputed
by OEM code over the honest `bit5 = 0`, so the frame stays self-consistent — no
DTC, no camera fault).

---

## 3. The patch

One site, three words. Offset is bytes into `14C217` blk1. Assert the stock
words before writing — a mismatch means the wrong image or a shifted layout.

| Offset | Site | Stock | Patched | Effect |
|---|---|---|---|---|
| `0x3B1C0` | `P:$2B8E0` | `F67C 2DAA 2250` | `E680 2250 E700` | store `0` to the transmitted hands-off cell instead of the detected value, then NOP |

```text
stock    F67C 2DAA 2250   MOVE.W X:$2DAA,X:$2250
patched  E680 2250 E700   MOVE.W #0,X:$2250 ; NOP
```

Context that must survive verbatim (the availability and deny copies on either
side, at `0x3B1B4`): `F67C 2D28 2252  F67C 2DB8 2251  F67C 2DAA 2250`.

---

## 4. Integrity repair

`14C217` blk1 carries two internal checksums the module verifies; both are
recomputed by the builder after patching, in this order, before the container
CRCs. (Same algorithms as the LCA enabler — see `PSCM_LCA_enabler.md` §5 for
the full definitions.)

- **Word A** @ `0x63FEA` — CRC-16/MCRF4XX over blk0 + blk1[`START`..wordA],
  `START = 0x1800`. Here `D110 -> 2C0D`.
- **Word B** @ `0x63FEC` — 16-bit LE sum over the whole module (blk0 + patched
  blk1 + blk2 + paired `14C218`), truncated at `0x0007FFEC`. Here `3216 -> 366D`.
- **Container** — per-block CRC-16/CCITT-FALSE + header CRC-32 file checksum.

---

## 5. Prebuilt image

```text
CV6T-14C217-AR_HANDSOFF_OFF.VBF
sha256 dbdeb8e1f889c81ad660f96fc79e2b11ddede75b8615f17923b9e75ec1da5895
```

Exactly **10 bytes** differ from stock: 6 patch bytes (3 words) plus the two
internal checksum words. Blocks 0 and 2 are byte-identical to stock.

Build and independently verify:

```bash
python3 work/lca_resume/build_handsoff_off_vbf.py --selftest
python3 work/lca_resume/build_handsoff_off_vbf.py
```

The builder reuses the proven checksum/container machinery in
`build_fd22_snapshot_vbf.py`, asserts every stock word and the neighbouring
availability/deny copies, and recomputes both internal checksums from scratch.

---

## 5a. Combined production images (LCA enabler + hands-off disable)

For production use the hands-off disable is shipped **combined with the LCA
enabler** in a single flash. The six patch sites are disjoint and independent
(LCA torque chain vs. the status-frame composer's hands-off mirror copy), so
they compose cleanly. Built for both revisions:

```text
CV6T-14C217-AR_LCA_HANDSOFF.VBF  sha256 a8192f9b99ec1d2cd9c68e51379df1b04145140f71286c639f5f3c812e91b152
HV6T-14C217-AC_LCA_HANDSOFF.VBF  sha256 ea375c61c1c07c85bb515f498c00e6b1cda09cded2c9deb63a3937371a865c91
```

- CV6T: 28 bytes differ from stock (24 patch + 4 checksum); blocks 0/2 identical.
- HV6T: 29 bytes differ from stock (25 patch + 4 checksum); blocks 0/2 identical.

The HV6T hands-off site was located **structurally** (isomorphic revision, RAM
cells +0x5A): the status-copy triple is at blk1 `0x3B3F8`, the hands-off copy at
`0x3B404` / `P:$2BA02` — `F67C 2E04 22AA -> E680 22AA E700`. Confirmed that
`X:$22AA` is written only there and read only by the composer at `P:$1DBF5`,
exactly mirroring CV6T's `X:$2250`.

Build and verify:

```bash
python3 work/lca_resume/build_lca_handsoff_cv6t_vbf.py --selftest
python3 work/lca_resume/build_lca_handsoff_cv6t_vbf.py
python3 work/lca_resume/build_lca_handsoff_hv6t_vbf.py --selftest
python3 work/lca_resume/build_lca_handsoff_hv6t_vbf.py
```

Each combined image was independently confirmed to equal the corresponding
LCA-only image plus exactly the hands-off patch (zero changes outside the
hands-off site and the two recomputed checksum words), and both pass
`vbflasher.py verify` (`ALL CRCs OK`).

> HV6T combined image: the LCA logic and the hands-off relocation are both
> structurally verified and the container is valid, but — as with the HV6T
> LCA-only image (`PSCM_LCA_enabler.md` §6a) — it has not yet been driven on a
> vehicle. The CV6T combined image carries the driven LCA control words plus the
> confirmed-working hands-off patch.

---

## 6. Flashing

Flashed over UDS at `0x730` with the standard Ford SBL. Any flasher that
handles Ford VBF containers will do; the repo's `VBFlasher` is proven on this
module.

```bash
cd /home/gl/Projects/ford/VBFlasher
python3 vbflasher.py verify /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_HANDSOFF_OFF.VBF   # -> ALL CRCs OK
python3 vbflasher.py flash --dry-run /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_HANDSOFF_OFF.VBF
python3 vbflasher.py flash /home/gl/Projects/ford/PSCM/Research/CV6T-14C217-AR_HANDSOFF_OFF.VBF
# power-cycle the ignition, then
python3 vbflasher.py dtc PSCM
```

Reverting is a normal flash of the stock `CV6T-14C217-AR.VBF`.

---

## 7. Expected result & verification

- Frame `0x140` byte 7 bit 5 reads **0** at all times (byte 7 = `0x04`, never
  `0x24`); the IPC hands-off warning never escalates.
- Availability (`bit 2`), deny (`bit 4`), checksum (byte 6) and rolling counter
  behave as stock; the frame remains valid, so no PSCM/IPMA DTC.
- Confirm on the bench/car with `work/vehicle/la_monitor.py` (decodes
  `LaHandsOff_B_Actl` from live `0x140`) — it should report `hands 0` steadily
  even with no hands on the wheel.

> This is a control-path modification to a steering module. Since it changes a
> safety-warning output, treat it as test-only: it defeats the driver-attention
> escalation, so use it only for controlled testing on your own vehicle.
