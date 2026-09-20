# `work/wordA/` — SOLVED

`14C217` blk1 **word A** is solved. Everything here is offline analysis; nothing touches the vehicle.

```
word_A = crc16_mcrf4xx( blk0 ++ blk1[START .. 0x63FEA), init=0xFFFF )
         stored little-endian at blk1+0x63FEA  (flash 0x0007FFEA)
```

Reflected CCITT (poly `0x1021`, init `0xFFFF`, xorout `0`) — the same engine and table as blk2's
word A. The message is **blk0 concatenated with the tail of blk1**, *skipping the 14C218
calibration* that sits between them in flash.

⚠ **`START` is per build.** `AF`/`AH` = `blk1+0x1000`, `AR` = `blk1+0x1800`. Assuming `0x1000`
yields `C8F0` instead of `D110` on AR. Recover it from the firmware's own code.

## Run it

```bash
python3 wordA_solved.py        # recovers START per image, verifies all 3 versions, exit 0
python3 verify_engine.py       # engine control + the word/byte equivalence proof
cd ../.. && python3 work/verify_dumps.py   # verifies against the LIVE vehicle dump
```

Verified: all three OEM versions **and** the live 512 KB flash dump (`CV6T-14C217-AR`: `D110`).

## How it was found

1. `find_region_consts.py` — searched for word A's address as a **word** immediate (`addr/2`, since
   the MC56F8366 is word-addressed). Found the self-check routine; byte-address searches had missed
   it entirely.
2. `find_loop.py` / `find_xref.py` — the verifier at `blk1+0x82A0` compares the stored word against
   RAM cell `X:$11EF`; that cell has exactly **one** store, right after `JSR P:$16216`.
3. `decode_word.py` — resolved the loop's operands (`Y1` = CRC, `R0` = table base `X:$5676` =
   `blk2+0x20EC`, `R2` = program pointer).
4. `find_calls.py` — recovered the call arguments (`START`, count) from the `MOVE.L #imm32` loads.
5. `test_compose.py` — swept region **compositions** x all 65536 seeds. `b0+b1[0x1000:A]` matched
   two versions exactly; the third needed its own `START`, which its code confirmed.

## Why the sweeps missed it

The message is a **gapped concatenation**, a shape none of the searches could express:

* `rangesweep.c` swept every `(start,end)` — but of **blk1 alone**, so never a blk0 prefix.
* the linear-image tests used one **contiguous** span, so always included the calibration.
* `msgsets.bin` *did* contain `b0_b1_*` compositions — but that run was killed at **poly 64 of
  65536**, so poly `0x1021` was never reached on them.

**An "exhaustive" sweep is only exhaustive over the shape of message it can express.** When the
algorithm is known and no range matches, vary the composition, not the parameters.

## Reading the logs

A bare `HIT` is not a finding: the stored word is 16 bits, so coincidences appear at roughly
`candidates / 65536`. A hit counts only if it matches **all three** versions, and for a range sweep
only if its `end` lands exactly on `0x63FEA`.

| Log | What it shows |
|---|---|
| `rs_b1.log` | Exhaustive `(start,end)` of blk1: 59 hits, **none** ending at `0x63FEA` — all noise. |
| `hyp.log` | All 65536 polys over `blk1[0x1000..0x63FEA)` alone: 0 hits (no blk0 prefix). |
| `shortrange.log` | Short-region CRCs: 0 hits. Incomplete (tool timeout). |

## Disassembler

`dis56800e.py` + `encodings.json` (**569 encodings, 148 mnemonics**) extracted mechanically from the
ERM's own opcode grids by `extract_encodings.py` — no hand transcription. `--selftest` runs JSR
containment (55/55) and cross-validates against `Research/pscm_mc56f8366_disasm_v1`'s four
independently hand-verified rules.

> ⚠ **`JSR <ABS19>` mask is `0xFFF4`, not `0xFFF5`.** The encoding is `1110 0010 0101 A1AA`: bits
> 3,1,0 are address bits 18,17,16 and bit 2 is the JSR/JMP literal. `0xFFF5` also pins bit 0 = 0 and
> silently skips `0xE255` — the common form in blk1. It scored 55/55 on the SBL anyway (every SBL
> target is `0x4xxxx`), so the self-test never caught it; with the wrong mask the CRC routine showed
> **0 call sites** instead of 7. A green self-test on one image does not validate a mask on another.
