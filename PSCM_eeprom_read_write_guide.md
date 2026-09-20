# Reading and writing PSCM EEPROM with the OEM SBL

**No custom bootloader is required.** The stock Ford secondary bootloader `BV6T-14C220-AA` already
exposes everything needed to read *and* write the PSCM's 1 KiB EEPROM. This document is the
procedure, derived from vendor-tool captures (`ucds_eeprom_read.log`, `ucds_eeprom_write.log`) and
cross-checked against the OEM VBF. Analysis of those captures is in
`PSCM_ucds_eeprom_procedure.md`.

> **Status of this document:** the sequences below are transcribed from a *verified* capture of a
> commercial tool performing both operations successfully on a real module. The read path is
> additionally confirmed against our own dumps. Nothing here has been re-executed by our own
> tooling yet — see [Before you run this](#before-you-run-this).

---

## 1. Facts you need

| Item | Value |
|---|---|
| ECU diagnostic address | tx `0x730` / rx `0x738`, CAN-HS (`can0`), 500 kbit |
| EEPROM region | `0x02000000`, length `0x400` (1024 bytes) |
| SBL file | `BV6T-14C220-AA.vbf` (`sw_part_type SBL`, `call 0x4F800`) |
| SBL load address | `0x0009F000`, length `0x0F82` (3970 bytes) |
| SBL sha256 | `7f0421b4192ec230972a9549ddf536899cd906ba56e903d7660e2959deedf226` |
| SecurityAccess | level `0x01`, constant `MAGIC_PSCM_L1 = 0x9B2533` (verified on-module) |
| PBL version this SBL targets | `6.00.20.04` (read it back with `22 F180`) |

### Address spaces

The EEPROM prefix is a **third** address space, distinct from the two already documented:

```
code   space:  byte = 2 x word
data   space:  byte = 0x04000000 + 2 x word
EEPROM      :  0x02000000 .. 0x020003FF          <- flat, 1024 bytes
```

The SBL's execute routine takes a **word** address: `0x0004F800 = 0x0009F000 / 2`, which is exactly
the `call` field in the VBF header. Getting this wrong is the classic "transport looks broken"
failure.

### Routine IDs

These are not in any public table; they came out of the capture.

| RID | Meaning |
|---|---|
| `0x0301` | Execute SBL at the given **word** address |
| `0xFF00` | eraseMemory (ISO 14229 standard RID) |
| `0x0304` | checkMemory / verify |

---

## 2. Common preamble — get the SBL running

Identical for both read and write.

```text
# 1. Identify — confirm the PBL matches the SBL you are about to load
730  22 F180
738  62 F180 "014BPIT-MC56F836-6.00.20.04"

# 2. Default session, then PROGRAMMING session
730  10 01            ->  738  50 01 0032 01F4
730  10 02            ->  738  50 02 0014 01F4

# 3. SecurityAccess level 1
730  27 01            ->  738  67 01 <seed3>
730  27 02 <key3>     ->  738  67 02

# 4. RequestDownload: SBL -> RAM at 0x0009F000, 3970 bytes
730  34 00 44 0009F000 00000F82
738  74 20 0FFE                      # maxNumberOfBlockLength = 4094

# 5. One single TransferData block (3970 < 4094, so it all fits)
730  36 01 <3970 bytes of BV6T-14C220-AA blk0>
738  76 01

# 6. RequestTransferExit — the ECU echoes CRC-16/CCITT-FALSE of what it received
730  37
738  77 6C48                         # == BV6T-14C220-AA.vbf blk0 crc16. Assert this.

# 7. Execute the SBL
730  31 01 0301 0004F800
738  71 01 0301 10
```

**Assert step 6.** `0x6C48` is the VBF's own stored `crc16`, so a match proves the SBL arrived
intact before you hand it the PC. Abort on any other value.

Get the SBL bytes with:

```bash
T=~/.hermes/skills/software-development/vbf-firmware-container/scripts/vbftool.py
python3 $T extract BV6T-14C220-AA.vbf -o work/sbl_oem/
# -> work/sbl_oem/BV6T-14C220-AA_blk0_0x0009F000.bin   (3970 bytes)
```

Key derivation (`work/pscm_seckey.py`, verified against two captured seed/key pairs):

```python
from pscm_seckey import key_from_seed
key = key_from_seed(seed3)          # default fixed = MAGIC_PSCM_L1 = 0x9B2533
```

---

## 3. Reading the EEPROM

```text
730  35 00 44 02000000 00000400     # RequestUpload, 1024 bytes
738  75 20 0082                     # maxBlockLength 0x82 = 130 => 128 payload bytes/block

730  36 01   ->  738  76 01 <128 bytes>
730  36 02   ->  738  76 02 <128 bytes>
...
730  36 08   ->  738  76 08 <128 bytes>          # 8 blocks x 128 = 1024

730  37      ->  738  77 0AD2                    # CRC-16/CCITT-FALSE of the 1024 bytes
```

**Verify the result:** the `37` response is the CRC-16/CCITT-FALSE of the uploaded data. Confirmed:

```python
import binascii
binascii.crc_hqx(open('eeprom.bin','rb').read(), 0xFFFF)   # -> 0x0AD2
```

That is a genuine end-to-end integrity check on the read — use it.

### Sanity-checking a dump

A good PSCM EEPROM dump is ~5 % `0xFF` and contains readable identity strings:

```
0x000  CV61-3C579-AL              (PSCM part number)
0x018  WF0AXXWPMAEL32600          (VIN)
0x030  CV61-3C579-AL
0x04B  CV6C-3D070-LF
0x06A  133402326H12ZR1FAK0G293120852
0x088  140102704M50664140111NCLF-5473t
```

If the VIN is absent or the dump is ~100 % `0xFF`, you read nothing. Total read time is ~0.3 s for
the transfer; the whole session including SBL load takes under 4 s.

---

## 4. Writing the EEPROM

```text
# 1. Erase the region
730  31 01 FF00 02000000 00000400
738  7F 31 78                        # responsePending — CONTINUE, not an error
738  71 01 FF00 10

# 2. RequestDownload for the same region
730  34 00 44 02000000 00000400
738  74 20 0082                      # 128 payload bytes per block

# 3. Eight blocks of 128 bytes
730  36 01 <128 bytes>  ->  738  76 01
...
730  36 08 <128 bytes>  ->  738  76 08

# 4. Exit
730  37
738  7F 37 78                        # responsePending — CONTINUE
738  77 2BF8

# 5. Verify
730  31 01 0304
738  7F 31 78
738  71 01 0304 10 02

# 6. Hard reset to bring the application back up
730  11 01   ->  738  51 01
```

### The `37` response on the write path is NOT a payload CRC

On the **read** path `77 0AD2` is exactly `crc_hqx(data, 0xFFFF)`. On the **write** path the module
answered `77 2BF8`, and that value is **not** reproduced by any simple checksum of the 1024 bytes
sent:

| algorithm over the written payload | value |
|---|---|
| CRC-16/CCITT-FALSE | `0x0AD2` |
| CRC-16/XMODEM | `0xBDBD` |
| CRC-16/IBM (ARC) | `0x1E11` |
| CRC-16/MODBUS | `0xCAAF` |
| sum16-BE | `0xCCB0` |
| **observed** | **`0x2BF8`** |

Its derivation is **unresolved**. Do not gate write success on it, and do not assume it means the
write failed — the capture's write completed successfully. **Use `31 01 0304` (checkMemory) as the
verification step, then read the region back and compare.**

### Read-back is the real acceptance test

```
write -> 11 01 reset -> re-read via section 3 -> compare against intended image
```

Bear in mind when comparing: **this region is volatile.** Two dumps of the same healthy module
taken months apart (`EEPROM_DUMP_02052026.bin` vs `EEPROM_DUMP_14092026.bin`) differ in **125 of
1024 bytes (12.2 %)** — adaptation data the application legitimately rewrites. So:

* Require the **identity region to match byte-exact** (part numbers, VIN — offsets `0x000`–`0x0A8`).
* Require **the bytes you intended to change** to hold their new values.
* **Tolerate** differences elsewhere; a naive whole-region `cmp` will always "fail".

---

## 5. Before you run this

* **Dump first, twice.** You cannot restore what you did not save. Keep the original with its CRC.
* **The captured write changed nothing.** The read payload and write payload in the source logs are
  byte-identical (0 differing bytes) — it was a read-back/write-back round trip. So the *sequence*
  is proven, but there is no worked example of an actual modified value, and no evidence about
  which cells the PSCM will accept or how it reacts to an edited one.
* **TesterPresent:** neither capture contains a single `3E` frame — the whole exchange finishes in
  under 4 s, well inside the `01F4` (500 ms x ...) timing the module reported. For anything slower
  than the captured flow, send suppressed-response `3E 80` at ~1.5 s in a background thread.
* **ISO-TP padding is mandatory** (`SOL_CAN_ISOTP = 106`, TX+RX padding). Ford ECUs silently ignore
  short-DLC frames, and the symptom is an unexplained timeout.
* **Always send `37` after a failed transfer**, otherwise the module stays "transfer active" and
  every later `34`/`35` returns NRC `0x22`.
* **Treat `7F .. 78` as CONTINUE** and reset the wait clock. It occurs on the erase and the
  transfer exit on the write path.
* Physical: vehicle stationary, ignition on, engine off, stable supply. A brown-out during an
  EEPROM erase/write is how a module is lost.
* `11 01` at the end is not optional on the write path — the application needs to restart to
  re-read its configuration.

## 6. Quick reference

```text
                    READ                              WRITE
preamble   22 F180 / 10 01 / 10 02 / 27 01 / 27 02
SBL        34 00 44 0009F000 00000F82 / 36 01 <3970B> / 37 (expect 77 6C48) / 31 01 0301 0004F800
                    -------------------------------------------------------
region              35 00 44 02000000 00000400        31 01 FF00 02000000 00000400
                    36 x8  (76 carries 128 B)         34 00 44 02000000 00000400
                    37 -> 77 0AD2 = CRC16             36 x8  (128 B each)
                                                      37 -> 77 xxxx (NOT a payload CRC)
                                                      31 01 0304
                                                      11 01
```

## See also

* `PSCM_ucds_eeprom_procedure.md` — analysis of the source captures, SBL identification, keygen
  verification
* `work/parse_ucds_log.py` — ISO-TP reassembly + UDS narration for candump logs
* `work/extract_ucds_transfers.py` — pulls `34`/`35`/`36`/`37` payloads out of a capture
* `work/pscm_seckey.py` — SecurityAccess key derivation
* `PSCM_flash_reading.md` — the code/data address mapping and full-flash dump procedure
