# CV6T PSCM: testing the cruise-coupled LCA hypothesis

## Conclusion

**A direct dependency of LCA on the cruise/ACC state inside PSCM 14C217/14C386 has been disproven.**

The PSCM with the `CV6T-14C386-AB` configuration does not subscribe to `0x1A0 ECM_h_FrP08`, where the DBC places `CcStat_D_Actl`, and does not subscribe to other frames with named `Cc*`/`Acc*` signals. The extraction table contains no record with the exact geometry of `CcStat_D_Actl`. The traced sections `CAN enum 6 -> lane state 4 -> per-state code 4/5 -> torque enable` read only lane-internal cells/flags; the cruise state is not among them. The control `BV6T-14C217-AF` is structured the same way.

This does not rule out a general inhibit based on speed, braking, CAN quality, driver override, etc., and does not prove the presence of actual LCA torque on the vehicle. It rules out the narrower hypothesis from `PSCM_LCA_why_no_torque.md`: **the PSCM cannot require `CcStat_D_Actl != 0`, because this signal is not received by the PSCM.** Cruise may remain a condition on the IPMA/CCM/BCM side, but it is not a PSCM gate found here.

## Artifacts and reproduction

OEM `.bin` files were read only; VBF/flash were not modified.

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/analyze_cruise_gate.py \
  > work/lca_resume/analyze_cruise_gate.out
python3 work/lca_resume/verify_cruise_gate_report.py
```

Additional existing tools used as an independent decoding check:

```bash
python3 work/disasm/signal_config.py
python3 work/disasm/extraction_table.py
python3 work/disasm/byteholder.py
python3 work/disasm/signal_usage.py
```

Source images:

- `bins/CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin`
- `bins/BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin`
- `bins/CV6T-14C217-AH/{blk0,blk1}`
- `bins/BV6T-14C217-AF/{blk0,blk1}`
- `bins/CV6T-14C217-AR/{blk0,blk1}` (address check against the known `X:$2DDE/$2DB9`)

The `P:$xxxxx` and `X:$xxxx` addresses below are **DSP56800E word addresses**. Words in the files are little-endian; the report shows decoded 16-bit values.

## 1. Where `CcStat_D_Actl` would have to be received

DBC `/home/gl/Projects/ford/CANBus/CAN-HS.dbc` specifies:

```text
BO_ 416 ECM_h_FrP08             # 416 = 0x1A0
SG_ CcStat_D_Actl : 10|3@0+
receivers: ABS,BCM,CCM,OCS,RCM,TCM   # PSCM is absent
```

Motorola geometry `10|3@0+`: payload byte 1, mask `0x0E`, shift `1`; the expected packed spec in the PSCM extraction record is **`0x0E01`**.

### Actual mailbox descriptor table in 14C386

CV6T contains 20 descriptor records, 16 of which are RX. The complete set of RX IDs:

```text
CV6T-14C386-AB: 010 080 0A5 0C8 160 170 180 190 1C0 1E0 400 405 420 696 730 7DF
BV6T-14C386-AA: 010 080 090 0A5 0C8 160 170 180 190 1C0 1E0 400 405     696 730 7DF
```

`0x1A0` is absent from both. `0x150` (`AccEnbl_B_RqDrv`) and `0x200` (`CcMde_D_Actl`) are also absent. Across all DBC signals of all configured RX frames, **0** names matching `Cc*`, `Acc*`, or `*Cruise*` were found (the service signal `CCP_PSCM_Rx` was explicitly excluded).

The positive control for the mailbox decoder is the actual `0x0A5`:

```text
CV6T X:$421A:
00A5 0000 3F6A 0005 7F4E 42BC 0000 0000 0020 0048
              ^buf      ^state ^mask              ^RX slot/flags

BV6T X:$421A:
00A5 0000 3F6E 0006 7F50 42B6 0000 0000 0040 0048
```

Thus, the method finds a lane frame known to be received at an actual address, but `0x1A0` is absent from all descriptor records.

## 2. Extraction: there is no exact `CcStat` record

All plausible stride-5 records with phase 0 in the `X:$4000..$41DF` range were checked:

```text
CV6T-14C386-AB: 71 records; spec 0E01: 0
BV6T-14C386-AA: 69 records; spec 0E01: 0
```

The positive sensitivity control is `LkaActvStats_D_Req`, DBC `30|3@0+`, spec `0x7004`:

```text
CV6T X:$4087: 0000 7004 0020 7ED7 7F75   &record[+3] = X:$408A
CV6T X:$408C: 0000 7004 0020 863C 7F8A   &record[+3] = X:$408F
BV6T X:$4087: 0000 7004 0040 7EDF 7F75   &record[+3] = X:$408A
BV6T X:$408C: 0000 7004 0040 8628 7F8A   &record[+3] = X:$408F
```

The DSP56800E pointer getter method is confirmed by CV6T-AH executable code:

```text
P:$1C7A7  E40A 408A 9D34 E256 EF95 ...   MOVE.L #$408A,R2; ... JSR P:$2EF95
P:$1C7AE  E40A 408F D5E0 0008 E256 EF95 MOVE.L #$408F,R2; ... JSR P:$2EF95

P:$2EF95  F816 B836 F9B4 7818 7C82 B832 7C88 ...
            ^ X:(R2)->R0   ^ payload byte       ^ mask/shift path
```

BV6T has the same helper structure at `P:$2B91E`:

```text
F816 B836 F9B4 7818 7C82 B832 7C88
```

Therefore, the negative result was not obtained by searching for a bare destination address: the table-driven extraction and its `&record[+3]` getter mechanism were specifically checked.

## 3. Enum 6 actually reaches the internal LCA state

### CV6T-14C217-AH

Dispatcher `P:$28CEB`, enum source `X:$28C3`, lane state `X:$28D6`:

```text
F07C 28C3
4C02 A203 E681 28D6 A910
4C04 A203 E682 28D6 A90B
4C06 A207 F07C 076A 4C01 A203 E684 28D6 A902
E680 28D6 E708
```

Meaning of the branches:

- enum `2` -> `X:$28D6 = 1`;
- enum `4` -> `X:$28D6 = 2`;
- enum `6` -> check of static/config cell `X:$076A == 1` -> `X:$28D6 = 4`;
- otherwise `X:$28D6 = 0`.

`X:$076A` is the same CV6T-only variant gate as `X:$0904` in AR; it is **not** an extracted cruise cell. There is no cruise source in 14C386 that could update it.

### BV6T-14C217-AF control

Dispatcher `P:$26911`, source `X:$23D1`, lane state `X:$23E4`:

```text
F07C 23D1
4C02 A203 E681 23E4 A90C
4C04 A203 E682 23E4 A907
4C06 A203 E684 23E4 A902
E680 23E4 E708
```

BV6T performs the same enum-6 -> internal-state-4 translation without an additional variant gate. The AH/BV difference here is known and is unrelated to cruise reception.

For checking address relocation in CV6T-AR: dispatcher `P:$2A772`, lane `X:$2DDE`, config gate `X:$0904`; this confirms the connection with the previously documented `X:$2DDE/$2DB9`.

## 4. State 4 -> sustained code 5: the cruise gate is absent

CV6T-AH has four downstream checks of `lane == 4`:

```text
P:$29170  P:$291EF  P:$29227  P:$29263     read X:$28D6; CMP #4
```

BV6T-AF control:

```text
P:$26D5A  P:$26DD9  P:$26E11  P:$26E4D     read X:$23E4; CMP #4
```

The three repeated CV6T-AH state-machine blocks that convert the entry/sustained state to code 5 share the same structure. The first, `P:$291EC`:

```text
00EE 5E81 A30B
F07C 28D6 4C04 A207       # lane state == 4
F07C 28B9 4C01 A303       # internal enable cell == 1
E700 4C83 A308            # compare internal B state (#131)
E686 28A7                 # internal state := 6
E685 28B1                 # per-state code := 5
E680 28AB
```

Actual branch targets:

```text
P:$291EE A30B -> P:$291FA
P:$291F2 A207 -> P:$291FA
P:$291F6 A303 -> P:$291FA
P:$291F9 A308 -> P:$29202
P:$291FA E686 28A7
P:$291FC E685 28B1
```

Two more isomorphic blocks: `P:$29224..$29234` and `P:$29260..$29270`. The fourth write of `#5` is in the adjacent branch at `P:$2928D`. None of the blocks reads an external cruise holder; the main LCA block contains only `X:$28D6`, internal enable `X:$28B9`, internal state, and the code cell.

BV6T-AF contains the same structure, for example at `P:$26DD6`:

```text
00E8 5E81 A30B
F07C 23E4 4C04 A207
F07C 23C7 4C01 A303
E700 4C83 A308
E686 23B5
E685 23BF
E680 23B9
```

The corresponding writes of `#5`: `P:$26DE6`, `$26E1E`, `$26E5A`, `$26E77`. The RAM operands/one counter differ, not the presence of a cruise condition.

## 5. The torque path accepts LKA code 2 and LCA code 5 identically

CV6T-AH, `P:$295BE`, per-state code `X:$28B1`:

```text
F07C 28B1
4C02 A303 E700
4C05 A203 E700
E581 A901 E580
```

BV6T-AF, `P:$274AE`, per-state code `X:$23BF`:

```text
F07C 23BF
4C02 A303 E700
4C05 A203 E700
E581 A901 E580
```

`code == 2` (LKA) and `code == 5` (sustained LCA) lead to `Y0 = 1`; all others lead to `Y0 = 0`.

Comparison of the entire function at `P:$29586` (AH) / `P:$27476` (BV), 107 words:

```text
14 differences at offsets:
+3 +5 +7 +15 +17 +19 +38 +40 +51 +55 +57 +68 +102 +105
```

All 14 differences occur after an identical opcode; 13 are relocated X-RAM operands, and one is a relocated JSR target. The key offset `+57`: `AH 28B1` / `BV 23BF`, with preceding opcode `F07C` in both. The executable torque-enable logic is identical; no cruise compare/branch was added.

## 6. Negative results and sensitivity

| Check | Scope | Result | Positive control |
|---|---:|---|---|
| RX mailbox descriptors | all 14C386 records; 16 RX in each family | `0x1A0` is absent from the AH configuration and BV control | `0x0A5` found in both at `X:$421A` |
| Named cruise/ACC signals | all DBC signals of all configured RX IDs | 0 | DBC associates `CcStat_D_Actl` with `0x1A0`; the receiver list also does not contain PSCM |
| Exact extraction spec `0E01` | 71 CV / 69 BV stride-5 records | 0 / 0 | `7004` lane enum found through actual records and getter callers |
| Enum-6 dispatcher | both complete blk0+blk1 images | one exact state dispatcher in each; cruise read is absent | known enums 2/4/6 and lane cells found |
| `lane == 4` consumers | full-word absolute-reference scan | 4 AH / 4 BV | addresses listed above |
| Torque-enable shape | both complete images | 1 AH / 1 BV; accepts 2 and 5 | 107-word cross-family function comparison |

Separately, a full raw-word scan for the word `0x01A0` produced 2 matches in AH and 8 in BV; the opcode filter classified some as accesses to the **X-RAM address** `X:$01A0` (`AH P:$10428`, eight BV sites). This is not a CAN-ID reference and is not evidence that the frame is received: CAN reception is specified by a descriptor in 14C386, where ID `0x1A0` is absent. This test is included to avoid mistaking an accidental literal/address match for a signal xref.

### Limitation of the negative conclusion

The 14C386 table settles the question of direct CAN input: without an RX descriptor, the `0x1A0` payload does not enter the PSCM extraction pipeline. However, static analysis does not assign semantic names to every internal inhibit. Therefore, the conclusion is phrased strictly as follows:

- **disproven:** `CcStat_D_Actl` or another explicitly cruise/ACC CAN state directly gates state 6, code 4->5, or torque enable inside these PSCM builds;
- **not ruled out:** a general braking/speed/quality/driver-override inhibit or cruise coupling in another ECU before `0x0A5` is generated.
