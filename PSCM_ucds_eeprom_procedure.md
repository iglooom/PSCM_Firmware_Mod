# UCDS PSCM EEPROM read/write — procedure analysis and SBL recovery

Source captures: `ucds_eeprom_read.log`, `ucds_eeprom_write.log` (candump, `can0`, ISO-TP 0x730 tx / 0x738 rx).
Tools: `work/parse_ucds_log.py` (ISO-TP reassembly + UDS narration), `work/extract_ucds_transfers.py` (34/35/36/37 payload extraction).

## Headline result

**The "custom SBL" is not custom.** The 3970-byte secondary bootloader UCDS downloads to RAM is
the **stock Ford SBL `BV6T-14C220-AA`**, byte-for-byte:

| | |
|---|---|
| Recovered from capture | `work/ucds_download_0009F000_0.bin` |
| OEM VBF block | `BV6T-14C220-AA.vbf` → `work/sbl_oem/BV6T-14C220-AA_blk0_0x0009F000.bin` |
| SHA-256 (both) | `7f0421b4192ec230972a9549ddf536899cd906ba56e903d7660e2959deedf226` |
| `cmp` | identical, 3970 / 3970 bytes |

Three independent confirmations beyond the hash:

1. Load address `0x0009F000` == VBF `blk0 load`.
2. Length `0x0F82` == VBF `blk0 len`.
3. The ECU's `37` RequestTransferExit response is `77 6C 48` — and the VBF's stored
   `crc16 = 0x6C48`. The module echoes the CRC-16/CCITT-FALSE of what it received, and it matches
   the shipped container's own checksum. (The same `77 6C 48` appears in both logs.)

The VBF header reads `description { Secondary bootloader for 6.00.20.04 PBL. }` and
`call 0x4F800`; the module answered `22 F180` with `...MC56F836-6.00.20.04`, so UCDS is simply
using the correct OEM SBL for the PBL version it found.

So there is nothing proprietary to recover here — but the *procedure* around it is the reusable
part, and it is fully reconstructed below.

## The procedure

Identical preamble in both logs:

```
22 F180                         -> 62 F180 "014BPIT-MC56F836-6.00.20.04"   identify PBL/app version
10 01                           -> 50 01 0032 01F4                          default session
10 02                           -> 50 02 0014 01F4                          PROGRAMMING session
27 01                           -> 67 01 <seed3>                            SecurityAccess request seed
27 02 <key3>                    -> 67 02                                    unlock (level 1)
34 00 44 0009F000 00000F82      -> 74 20 0FFE                               RequestDownload, SBL -> RAM
36 01 <3970 bytes>              -> 76 01                                    one single block
37                              -> 77 6C48                                  exit, ECU echoes CRC16
31 01 0301 0004F800             -> 71 01 03 01 10                           RoutineControl: EXECUTE SBL
```

Note `74 20 0FFE`: maxNumberOfBlockLength = 4094, so the entire 3970-byte SBL ships in a **single**
`36` block. And `0x0004F800` is exactly the VBF's `call` field — the **word** address of byte
`0x0009F000` (`0x9F000 / 2`), which re-confirms the DSP56800E word/byte mapping documented in
`PSCM_flash_reading.md`.

### Read path (`ucds_eeprom_read.log`)

```
35 00 44 02000000 00000400      -> 75 20 0082      RequestUpload, EEPROM @0x02000000, 1024 bytes
36 x8                           -> 76 <128 bytes>  8 blocks (0x82 = 130 => 128 payload bytes)
37                              -> 77 0AD2         exit, CRC16 of the uploaded data
```

### Write path (`ucds_eeprom_write.log`)

```
31 01 FF00 02000000 00000400    -> 7F 31 78 / 71 01 FF00 10   eraseMemory routine (Ford std RID FF00)
34 00 44 02000000 00000400      -> 74 20 0082                 RequestDownload, same region
36 x8                           -> 76                         8 x 128 bytes
37                              -> 7F 37 78 / 77 2BF8         exit
31 01 0304                      -> 71 01 0304 10 02           checkMemory / verify routine
11 01                           -> 51 01                      hard ECU reset
```

`7F .. 78` responsePending appears on the erase and on the transfer-exit — expected, must be
treated as CONTINUE.

## EEPROM region

`0x02000000`, 1024 bytes — a third address prefix, distinct from code (`byte = 2 x word`) and
data (`0x04000000 + 2 x word`). 4.8 % `0xFF`. Contains the identity strings:

```
0x000  CV61-3C579-AL          0x018  WF0AXXWPMAEL32600 (VIN)
0x030  CV61-3C579-AL          0x04B  CV6C-3D070-LF
0x06A  133402326H12ZR1FAK0G293120852
0x088  140102704M50664140111NCLF-5473t
```

The uploaded payload is byte-identical to `EEPROM_DUMP_14092026.bin`, and the read and write
payloads are **identical (0 differing bytes)** — this capture is a read-back / write-back round
trip with no content change, i.e. a procedure test rather than a real edit.

## Security access — constant now verified on the real module

The captures give two genuine seed/key pairs, which is the live confirmation that
`work/pscm_seckey.py` was waiting for (its docstring flagged `MAGIC_PSCM_L1` as unverified,
third-party-table-sourced):

| seed | key in capture | `key_from_seed()` | |
|---|---|---|---|
| `1B0829` | `A22C7F` | `A22C7F` | OK |
| `1BB528` | `DB521B` | `DB521B` | OK |

`MAGIC_PSCM_L1 = 0x9B2533` is **correct for ECU 0x730 level 0x01**, proven against the module.

## Practical takeaways

* No custom bootloader is needed to read or write PSCM EEPROM — the OEM SBL already exposes
  `35` RequestUpload *and* `34`/`FF00` erase+download on the `0x02000000` region.
* RID `0x0301` = execute-SBL-at-word-address; RID `0xFF00` = erase; RID `0x0304` = verify.
* `EEPROM_DUMP_02052026.bin` vs `EEPROM_DUMP_14092026.bin` differ in 125 of 1024 bytes (12.2 %),
  consistent with the documented ~15 % volatile-adaptation fraction — so "dump twice and compare"
  is not a valid pass/fail test for this region.
